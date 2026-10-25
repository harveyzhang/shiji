import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from shiji.writer import ToklensWriter, default_writer

SOURCES = [{'id': 'src-1', 'purpose': 'identify', 'site': 'a.com', 'title': '煤油灯', 'snippet': '煤油灯是一种照明工具'}]


def reply(content, status=200):
    return httpx.Response(status, json={'choices': [{'message': {'content': content}}]})


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.key_file = Path(tempfile.mkdtemp())/'toklens.env'
        self.key_file.write_text('TOKLENS_API_KEY=k-test')
        self.sent = []

    def writer(self, handler):
        def record(request):
            self.sent.append(json.loads(request.content))
            return handler(request)
        return ToklensWriter(self.key_file, transport=httpx.MockTransport(record), sleep=lambda s: None)

    def test_sends_only_this_cases_sources_under_the_evidence_rules(self):
        self.writer(lambda r: reply('{}'))('煤油灯', 'object', ['玻璃灯罩'], SOURCES)
        system, user = self.sent[0]['messages'][0]['content'], json.loads(self.sent[0]['messages'][1]['content'])
        self.assertIn('只能根据给你的来源摘要', system)
        self.assertEqual((user['term'], user['sources']), ('煤油灯', SOURCES))

    def test_drafts_are_trimmed_and_shaped(self):
        answer = {'identity': {'text': '这是一盏煤油灯'*10, 'evidenceIds': ['src-1', 2]},
                  'era': {'text': '清末传入', 'evidenceIds': [], 'conflict': 'yes'},
                  'context': {'text': '放在桌上照明', 'evidenceIds': ['src-1']}}
        drafts = self.writer(lambda r: reply(json.dumps(answer, ensure_ascii=False)))('煤油灯', 'object', [], SOURCES)
        self.assertEqual(len(drafts['identity']['text']), 40)
        self.assertEqual(drafts['identity']['evidenceIds'], ['src-1', 'src-2'])
        self.assertIs(drafts['era']['conflict'], True)

    def test_scene_suggestion_is_shaped(self):
        answer = {'scene': {'preset': 'street_station', 'objects': [
            {'kind': 'bench', 'label': '候车大厅里的长椅'*3, 'note': '说明'*40, 'evidenceIds': ['src-1', 3]}]*6}}
        scene = self.writer(lambda r: reply(json.dumps(answer, ensure_ascii=False)))('站', 'old_photo', [], SOURCES)['scene']
        self.assertEqual((scene['preset'], len(scene['objects'])), ('street_station', 4))
        first = scene['objects'][0]
        self.assertEqual((len(first['label']), len(first['note']), first['evidenceIds']), (12, 40, ['src-1', 'src-3']))

    def test_era_views_are_kept_capped_and_shaped(self):
        views = [{'text': '1906年建成'*5, 'evidenceIds': [1]}, {'text': '1903年建成', 'evidenceIds': ['src-2']}, 'bad',
                 {'text': ''}, {'text': 'c'}, {'text': 'd'}, {'text': 'e'}]
        answer = json.dumps({'era': {'text': '说法不一', 'evidenceIds': [], 'conflict': True, 'views': views}}, ensure_ascii=False)
        drafts = self.writer(lambda r: reply(answer))('煤油灯', 'object', [], SOURCES)
        self.assertEqual(drafts['era']['views'][:2], [{'text': ('1906年建成'*5)[:20], 'evidenceIds': ['src-1']},
                                                      {'text': '1903年建成', 'evidenceIds': ['src-2']}])
        self.assertEqual(len(drafts['era']['views']), 3)

    def test_bare_source_numbers_are_read_as_source_ids(self):
        answer = json.dumps({'identity': {'text': '煤油灯', 'evidenceIds': [1, '2', 'src-3', ' 4 ']},
                             'scene': {'preset': 'historic_room', 'objects': [{'kind': 'lamp', 'label': '灯', 'note': '', 'evidenceIds': ['5']}]}})
        drafts = self.writer(lambda r: reply(answer))('煤油灯', 'object', [], SOURCES)
        self.assertEqual(drafts['identity']['evidenceIds'], ['src-1', 'src-2', 'src-3', 'src-4'])
        self.assertEqual(drafts['scene']['objects'][0]['evidenceIds'], ['src-5'])

    def test_missing_parts_become_empty_drafts(self):
        drafts = self.writer(lambda r: reply('{}'))('煤油灯', 'object', [], SOURCES)
        self.assertEqual(drafts['context'], {'text': '', 'evidenceIds': []})
        self.assertIs(drafts['era']['conflict'], False)

    def test_failures_never_echo_the_key(self):
        with self.assertRaises(RuntimeError) as caught:
            self.writer(lambda r: httpx.Response(401, text='bad key k-test'))('煤油灯', 'object', [], SOURCES)
        self.assertNotIn('k-test', str(caught.exception))
        self.assertIn('归纳', str(caught.exception))

    def test_writer_is_off_unless_a_key_file_is_configured(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(default_writer())
        with mock.patch.dict(os.environ, {'SHIJI_VISION_KEY_FILE': str(self.key_file)}, clear=True):
            self.assertIsInstance(default_writer(), ToklensWriter)


if __name__ == '__main__':
    unittest.main()
