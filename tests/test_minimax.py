import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from shiji.minimax import FirstThatWorks, MiniMaxVision, MiniMaxWriter, default_observe, default_write
from shiji.vision import ToklensVision
from shiji.writer import ToklensWriter

SOURCES = [{'id': 'src-1', 'purpose': 'identify', 'site': 'a.com', 'title': '煤油灯', 'snippet': '煤油灯是一种照明工具'}]


def key_file(line):
    path = Path(tempfile.mkdtemp())/'key.env'
    path.write_text(line)
    return path


class MiniMaxTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.key = key_file('# 注释\nMINIMAX_API_KEY=k-test\n')

    def transport(self, answer):
        def record(request):
            self.sent.append((str(request.url), request.headers.get('authorization'), json.loads(request.content)))
            return answer(request)
        return httpx.MockTransport(record)

    def test_the_writer_uses_the_cn_chat_endpoint_and_the_m31_model(self):
        reply = {'choices': [{'message': {'content': json.dumps({'identity': {'text': '煤油灯', 'evidenceIds': ['src-1']}}, ensure_ascii=False),
                                          'reasoning_content': '先想一想'}}]}
        writer = MiniMaxWriter(self.key, transport=self.transport(lambda r: httpx.Response(200, json=reply)), sleep=lambda s: None)
        drafts = writer('煤油灯', 'object', [], SOURCES)
        url, auth, body = self.sent[0]
        self.assertEqual((url, auth, body['model']), ('https://api.minimax.cn/v1/chat/completions', 'Bearer k-test', 'MiniMax-M3.1-Flash'))
        self.assertEqual(drafts['identity'], {'text': '煤油灯', 'evidenceIds': ['src-1']})

    def test_vision_sends_the_image_to_the_token_plan_vlm_endpoint(self):
        reply = {'content': '```json\n{"observations": ["拱形大窗"], "term": "古董火车站"}\n```', 'base_resp': {'status_code': 0, 'status_msg': 'success'}}
        vision = MiniMaxVision(self.key, transport=self.transport(lambda r: httpx.Response(200, json=reply)), sleep=lambda s: None)
        seen = vision(b'\xff\xd8not-really-a-jpeg', 'old_photo')
        url, auth, body = self.sent[0]
        self.assertEqual((url, auth), ('https://api.minimax.cn/v1/coding_plan/vlm', 'Bearer k-test'))
        self.assertTrue(body['image_url'].startswith('data:image/jpeg;base64,'))
        self.assertIn('不识别人物身份', body['prompt'])
        self.assertEqual(seen, {'observations': ['拱形大窗'], 'term': '火车站'})

    def test_a_vlm_error_code_is_a_failure_that_never_echoes_the_key(self):
        reply = {'content': '', 'base_resp': {'status_code': 1004, 'status_msg': 'auth failed k-test'}}
        vision = MiniMaxVision(self.key, transport=self.transport(lambda r: httpx.Response(200, json=reply)), sleep=lambda s: None)
        with self.assertRaises(RuntimeError) as caught:
            vision(b'jpeg', 'object')
        self.assertIn('1004', str(caught.exception))
        self.assertNotIn('k-test', str(caught.exception))

    def test_rate_limits_are_retried_like_toklens(self):
        answers = iter([httpx.Response(429, headers={'Retry-After': '1'}),
                        httpx.Response(200, json={'content': '{"observations": [], "term": "煤油灯"}', 'base_resp': {'status_code': 0}})])
        waits = []
        vision = MiniMaxVision(self.key, transport=self.transport(lambda r: next(answers)), sleep=waits.append)
        self.assertEqual(vision(b'jpeg', 'object')['term'], '煤油灯')
        self.assertEqual(waits, [1.0])


class FallbackTests(unittest.TestCase):
    def test_the_second_model_answers_when_the_first_fails(self):
        def first(*args):
            raise RuntimeError('识图请求失败（HTTP 429）。')
        self.assertEqual(FirstThatWorks([first, lambda *args: {'term': '煤油灯'}])(b'jpeg', 'object'), {'term': '煤油灯'})

    def test_the_last_failure_is_raised_when_every_model_fails(self):
        def failing(message):
            def call(*args):
                raise RuntimeError(message)
            return call
        with self.assertRaises(RuntimeError) as caught:
            FirstThatWorks([failing('一'), failing('二')])(b'jpeg', 'object')
        self.assertEqual(str(caught.exception), '二')

    def test_minimax_comes_first_and_missing_key_files_are_skipped(self):
        minimax, toklens = key_file('MINIMAX_API_KEY=k-a'), key_file('TOKLENS_API_KEY=k-b')
        both = {'SHIJI_MINIMAX_KEY_FILE': str(minimax), 'SHIJI_VISION_KEY_FILE': str(toklens)}
        with mock.patch.dict(os.environ, both, clear=False):
            self.assertEqual([type(m) for m in default_observe().models], [MiniMaxVision, ToklensVision])
            self.assertEqual([type(m) for m in default_write().models], [MiniMaxWriter, ToklensWriter])
        with mock.patch.dict(os.environ, {**both, 'SHIJI_MINIMAX_KEY_FILE': str(minimax)+'.missing'}, clear=False):
            self.assertEqual([type(m) for m in default_observe().models], [ToklensVision])
        with mock.patch.dict(os.environ, {'SHIJI_MINIMAX_KEY_FILE': '', 'SHIJI_VISION_KEY_FILE': ''}, clear=False):
            self.assertIsNone(default_observe())
            self.assertIsNone(default_write())


if __name__ == '__main__':
    unittest.main()
