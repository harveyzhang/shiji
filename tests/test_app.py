import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from shiji.app import create_app, default_search
from shiji.contract import validate_case
from tests.test_investigate import LAMP, FakeSearch


class AppTests(unittest.TestCase):
    def client(self, **options):
        return TestClient(create_app(search=FakeSearch(LAMP.answers), **options))

    def ask(self, client, files=None, **fields):
        return client.post('/api/investigate', data={'inputType': 'object', **fields}, files=files)

    def test_health_reports_which_parts_are_switched_on(self):
        self.assertEqual(self.client().get('/api/health').json(),
                         {'ok': True, 'search': True, 'vision': False, 'writer': False})

    def test_writer_words_the_claims_when_switched_on(self):
        def write(term, input_type, observations, sources):
            return {'identity': {'text': '这是一盏煤油灯', 'evidenceIds': [sources[0]['id'], sources[1]['id']]},
                    'era': {'text': '', 'evidenceIds': [], 'conflict': False}, 'context': {'text': '', 'evidenceIds': []}}
        response = self.ask(self.client(write=write), userHint='煤油灯')
        self.assertEqual(response.json()['claims'][0]['text'], '这是一盏煤油灯')

    def test_investigate_returns_a_contract_case(self):
        response = self.ask(self.client(), userHint='煤油灯')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(validate_case(response.json()), [])
        self.assertEqual(response.json()['status'], 'text_only')

    def test_unknown_input_type_is_rejected(self):
        response = self.client().post('/api/investigate', data={'inputType': 'video', 'userHint': '煤油灯'})
        self.assertEqual(response.status_code, 422)

    def test_uploaded_image_reaches_the_observer(self):
        seen = []

        def observe(image, input_type):
            seen.append(image)
            return {'observations': ['玻璃灯罩'], 'term': '煤油灯'}

        client = TestClient(create_app(search=FakeSearch(LAMP.answers), observe=observe))
        response = self.ask(client, files={'image': ('lamp.jpg', b'\xff\xd8jpeg', 'image/jpeg')})
        self.assertEqual((response.json()['status'], seen), ('ok', [b'\xff\xd8jpeg']))

    def test_non_image_upload_is_refused(self):
        response = self.ask(self.client(), files={'image': ('a.txt', b'hello', 'text/plain')})
        self.assertEqual(response.status_code, 415)

    def test_oversized_image_is_refused(self):
        response = self.ask(self.client(max_image_bytes=10), files={'image': ('a.jpg', b'x'*11, 'image/jpeg')})
        self.assertEqual(response.status_code, 413)

    def test_each_visitor_has_an_hourly_limit(self):
        client = self.client(hourly_cases=1)
        self.ask(client, userHint='煤油灯')
        self.assertEqual(self.ask(client, userHint='煤油灯').status_code, 429)

    def test_visitors_behind_the_trusted_proxy_are_counted_separately(self):
        client = self.client(hourly_cases=1, trusted_proxies=('testclient',))
        ask = lambda ip: client.post('/api/investigate', data={'inputType': 'object', 'userHint': '煤油灯'}, headers={'X-Real-IP': ip})
        self.assertEqual([ask('1.1.1.1').status_code, ask('2.2.2.2').status_code, ask('1.1.1.1').status_code], [200, 200, 429])

    def test_forwarded_ip_from_an_untrusted_client_is_ignored(self):
        client = self.client(hourly_cases=1)
        ask = lambda ip: client.post('/api/investigate', data={'inputType': 'object', 'userHint': '煤油灯'}, headers={'X-Real-IP': ip})
        self.assertEqual([ask('1.1.1.1').status_code, ask('2.2.2.2').status_code], [200, 429])

    def test_quota_caps_can_be_lowered_for_a_server(self):
        folder = Path(tempfile.mkdtemp())
        (folder/'wsa.env').write_text('TENCENTCLOUD_WSA_APIKEY=k-test\n')
        env = {'SHIJI_WSA_KEY_FILE': str(folder/'wsa.env'), 'SHIJI_LEDGER': str(folder/'ledger.json'),
               'SHIJI_MONTH_CAP': '1500', 'SHIJI_DAY_CAP': '300'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_search().caps, (1500, 300))

    def test_stream_sends_each_stage_then_the_result(self):
        response = self.client().post('/api/investigate/stream', data={'inputType': 'object', 'userHint': '煤油灯'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/event-stream', response.headers['content-type'])
        blocks = [b for b in response.text.split('\n\n') if b.startswith('event: ')]
        names = [b.split('\n', 1)[0][len('event: '):] for b in blocks]
        self.assertEqual((names[0], names[-1]), ('search', 'result'))
        self.assertEqual(validate_case(json.loads(blocks[-1].split('data: ', 1)[1])), [])

    def test_stream_checks_the_upload_before_streaming(self):
        response = self.client().post('/api/investigate/stream', data={'inputType': 'object'},
                                      files={'image': ('a.txt', b'x', 'text/plain')})
        self.assertEqual(response.status_code, 415)

    def test_finished_cases_are_archived_without_the_image_and_can_be_shared(self):
        archive = Path(tempfile.mkdtemp())
        client = self.client(archive_dir=archive)
        case = self.ask(client, files={'image': ('lamp.jpg', b'\xff\xd8secret-bytes', 'image/jpeg')}, userHint='煤油灯').json()
        self.assertRegex(case['shareId'], r'^[A-Za-z0-9_-]{12}$')
        shared = client.get(f"/api/cases/{case['shareId']}").json()
        self.assertEqual(shared['caseId'], case['caseId'])
        self.assertNotIn('secret-bytes', (archive/f"{case['shareId']}.json").read_text(encoding='utf-8'))
        self.assertIn('text/html', client.get(f"/c/{case['shareId']}").headers['content-type'])

    def test_streamed_result_carries_a_share_id(self):
        client = self.client(archive_dir=Path(tempfile.mkdtemp()))
        text = client.post('/api/investigate/stream', data={'inputType': 'object', 'userHint': '煤油灯'}).text
        result = json.loads([b for b in text.split('\n\n') if b.startswith('event: result')][0].split('data: ', 1)[1])
        self.assertEqual(client.get(f"/api/cases/{result['shareId']}").status_code, 200)

    def test_unknown_or_malformed_share_ids_are_not_found(self):
        client = self.client(archive_dir=Path(tempfile.mkdtemp()))
        for share_id in ('nope-nope-nope', '..%2F..%2Fetc', 'a'):
            with self.subTest(share_id=share_id):
                self.assertEqual(client.get(f'/api/cases/{share_id}').status_code, 404)

    def test_home_page_follows_the_investigation_live(self):
        response = self.client().get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('/api/investigate/stream', response.text)

    def test_sample_images_offered_on_the_home_page_are_served(self):
        client = self.client()
        paths = set(re.findall(r'/static/samples/[\w.-]+\.jpg', client.get('/').text))
        self.assertEqual(len(paths), 2)
        for path in paths:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['content-type'], 'image/jpeg')

    def test_three_js_is_served_from_this_server(self):
        for path in ('/static/vendor/three/three.module.js', '/static/vendor/three/three.core.js',
                     '/static/vendor/three/OrbitControls.js'):
            with self.subTest(path=path):
                response = self.client().get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('javascript', response.headers['content-type'])

    def test_front_end_dev_server_is_allowed_by_cors(self):
        response = self.client().options('/api/investigate', headers={
            'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST'})
        self.assertEqual(response.headers.get('access-control-allow-origin'), 'http://localhost:5173')


if __name__ == '__main__':
    unittest.main()


class OrderTests(unittest.TestCase):
    """An OPC service has to take orders: only what the operator needs to follow up, nothing more."""

    def client(self, **options):
        return TestClient(create_app(search=FakeSearch(LAMP.answers), **options))

    def test_an_order_is_saved_with_only_the_fields_asked_for(self):
        orders = Path(tempfile.mkdtemp())
        response = self.client(order_dir=orders).post('/api/orders', data={
            'plan': 'review', 'name': '王', 'contact': 'wx: abc', 'note': '想确认拍摄地点', 'shareId': 'FQbl1TBds6i3', 'idcard': '110'})
        self.assertEqual(response.status_code, 200)
        saved = [json.loads(p.read_text(encoding='utf-8')) for p in orders.glob('*.json')]
        self.assertEqual(len(saved), 1)
        self.assertEqual(set(saved[0]) - {'orderId', 'createdAt'}, {'plan', 'name', 'contact', 'note', 'shareId'})
        self.assertEqual(response.json()['orderId'], saved[0]['orderId'])

    def test_an_order_needs_a_contact_a_known_plan_and_a_real_share_id(self):
        client = self.client(order_dir=Path(tempfile.mkdtemp()))
        for data in ({'plan': 'review', 'contact': ' '}, {'plan': 'spaceship', 'contact': 'wx: abc'},
                     {'plan': 'review', 'contact': 'wx: abc', 'shareId': '../etc'}):
            with self.subTest(data=data):
                self.assertEqual(client.post('/api/orders', data=data).status_code, 422)

    def test_orders_are_limited_per_visitor(self):
        client = self.client(order_dir=Path(tempfile.mkdtemp()))
        codes = [client.post('/api/orders', data={'plan': 'review', 'contact': 'wx: abc'}).status_code for _ in range(6)]
        self.assertEqual(codes, [200]*5+[429])

    def test_ordering_is_closed_without_somewhere_to_keep_orders(self):
        self.assertEqual(self.client().post('/api/orders', data={'plan': 'review', 'contact': 'wx: abc'}).status_code, 503)
