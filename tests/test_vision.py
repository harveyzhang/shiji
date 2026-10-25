import base64
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

import httpx
from PIL import Image

from shiji.vision import DEFAULT_MODEL, ToklensVision, default_vision


def reply(content, status=200):
    return httpx.Response(status, json={'choices': [{'message': {'content': content}}]})


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.key_file = self.dir/'toklens.env'
        self.key_file.write_text('TOKLENS_API_KEY=k-test')
        self.sent = []

    def vision(self, handler, **options):
        def record(request):
            self.sent.append(json.loads(request.content))
            self.headers = request.headers
            return handler(request)
        return ToklensVision(self.key_file, transport=httpx.MockTransport(record), **options)

    def test_sends_the_image_with_the_key_and_default_model(self):
        seen = self.vision(lambda r: reply('{"observations": ["玻璃灯罩"], "term": "煤油灯"}'))(b'\xff\xd8jpeg', 'object')
        self.assertEqual(seen, {'observations': ['玻璃灯罩'], 'term': '煤油灯'})
        body = self.sent[0]
        self.assertEqual((body['model'], self.headers['Authorization']), (DEFAULT_MODEL, 'Bearer k-test'))
        self.assertTrue(body['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

    def test_png_and_webp_images_keep_their_type(self):
        for image, mime in ((b'\x89PNG\r\n', 'image/png'), (b'RIFF0000WEBPVP8 ', 'image/webp')):
            with self.subTest(mime=mime):
                self.vision(lambda r: reply('{"observations": [], "term": "x"}'))(image, 'object')
                self.assertTrue(self.sent[-1]['messages'][1]['content'][1]['image_url']['url'].startswith(f'data:{mime};base64,'))

    def test_prompt_forbids_identifying_people(self):
        self.vision(lambda r: reply('{"observations": [], "term": "x"}'))(b'jpeg', 'old_photo')
        self.assertIn('不识别人物身份', self.sent[0]['messages'][0]['content'])

    def test_json_inside_code_fences_is_parsed(self):
        seen = self.vision(lambda r: reply('```json\n{"observations": ["木质长椅"], "term": "火车站候车室"}\n```'))(b'jpeg', 'old_photo')
        self.assertEqual(seen['term'], '火车站候车室')

    def test_description_words_are_trimmed_from_the_search_term(self):
        seen = self.vision(lambda r: reply('{"observations": [], "term": "苏州博物馆游戏场景"}'))(b'jpeg', 'old_photo')
        self.assertEqual(seen['term'], '苏州博物馆')

    def test_collector_adjectives_are_trimmed_so_the_term_reads_like_the_sources(self):
        for raw, cleaned in (('古董煤油灯', '煤油灯'), ('复古台灯', '台灯'), ('老式座钟', '座钟'), ('老火车站', '老火车站'), ('古董', '')):
            with self.subTest(raw=raw):
                answer = json.dumps({'observations': [], 'term': raw}, ensure_ascii=False)
                self.assertEqual(self.vision(lambda r, answer=answer: reply(answer))(b'jpeg', 'object')['term'], cleaned)

    def test_generic_names_are_never_searched(self):
        for raw in ('老照片', '照片', '人物合影', '老物件', '古董', '图片', '老'):
            with self.subTest(raw=raw):
                answer = json.dumps({'observations': ['两位女子身着刺绣服饰'], 'term': raw}, ensure_ascii=False)
                self.assertEqual(self.vision(lambda r, answer=answer: reply(answer))(b'jpeg', 'old_photo')['term'], '')

    def test_prompt_asks_for_clothing_or_objects_when_the_photo_is_of_people(self):
        self.vision(lambda r: reply('{"observations": [], "term": "煤油灯"}'))(b'jpeg', 'old_photo')
        prompt = json.dumps(self.sent[0], ensure_ascii=False)
        self.assertIn('照片主体是人', prompt)

    def test_observations_are_capped_in_number_and_length(self):
        many = json.dumps({'observations': ['很长的观察'*20]*8, 'term': '铜壶'}, ensure_ascii=False)
        seen = self.vision(lambda r: reply(many))(b'jpeg', 'object')
        self.assertEqual(len(seen['observations']), 5)
        self.assertTrue(all(len(o) <= 30 for o in seen['observations']))

    def test_failures_never_echo_the_key(self):
        for response in (httpx.Response(401, text='bad key k-test'), reply('对不起，我看不清')):
            with self.subTest(status=response.status_code), self.assertRaises(RuntimeError) as caught:
                self.vision(lambda r, resp=response: resp)(b'jpeg', 'object')
            self.assertNotIn('k-test', str(caught.exception))

    def test_large_images_are_shrunk_to_a_jpeg_before_upload(self):
        buffer = BytesIO()
        Image.new('RGB', (3000, 2000), 'white').save(buffer, 'PNG')
        self.vision(lambda r: reply('{"observations": [], "term": "x"}'))(buffer.getvalue(), 'object')
        url = self.sent[0]['messages'][1]['content'][1]['image_url']['url']
        self.assertTrue(url.startswith('data:image/jpeg;base64,'))
        self.assertEqual(max(Image.open(BytesIO(base64.b64decode(url.split(',', 1)[1]))).size), 1024)

    def test_rate_limits_are_retried_after_the_requested_wait(self):
        answers = iter([httpx.Response(429, headers={'Retry-After': '3'}), reply('{"observations": [], "term": "煤油灯"}')])
        waits = []
        seen = self.vision(lambda r: next(answers), sleep=waits.append)(b'jpeg', 'object')
        self.assertEqual((seen['term'], waits), ('煤油灯', [3.0]))

    def test_gives_up_after_three_attempts(self):
        waits = []
        with self.assertRaises(RuntimeError) as caught:
            self.vision(lambda r: httpx.Response(429), sleep=waits.append)(b'jpeg', 'object')
        self.assertIn('429', str(caught.exception))
        self.assertEqual((len(self.sent), len(waits)), (3, 2))

    def test_vision_is_off_unless_a_key_file_is_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(default_vision())
        with mock.patch.dict(os.environ, {'SHIJI_VISION_KEY_FILE': str(self.key_file)}, clear=True):
            self.assertIsInstance(default_vision(), ToklensVision)


if __name__ == '__main__':
    unittest.main()
