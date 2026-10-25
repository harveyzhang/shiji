import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from shiji.search import BudgetExceeded, YuanbaoSearch


def reply(*titles):
    pages = [json.dumps({'title': t, 'url': f'https://e.com/{i}', 'passage': t}, ensure_ascii=False) for i, t in enumerate(titles)]
    return {'Response': {'Pages': pages, 'RequestId': 'r'}}


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir/'wsa.env').write_text('TENCENTCLOUD_WSA_APIKEY=k-test\n')
        self.sent = []

    def search(self, handler, **options):
        def record(request):
            self.sent.append(request)
            return handler(request)
        return YuanbaoSearch(self.dir/'wsa.env', self.dir/'ledger.json', transport=httpx.MockTransport(record), **options)

    def spent(self):
        return json.loads((self.dir/'ledger.json').read_text())['month_count']

    def test_query_is_booked_then_sent_with_the_key(self):
        found = self.search(lambda r: httpx.Response(200, json=reply('煤油灯')))('煤油灯 历史')
        self.assertEqual(found[0]['title'], '煤油灯')
        self.assertEqual(json.loads(self.sent[0].content), {'Query': '煤油灯 历史'})
        self.assertEqual(self.sent[0].headers['Authorization'], 'Bearer k-test')
        self.assertEqual(self.spent(), 1)

    def test_ledger_records_shiji_as_the_caller_by_default(self):
        self.search(lambda r: httpx.Response(200, json=reply('a')))('一')
        self.assertEqual(json.loads((self.dir/'ledger.json').read_text())['recent'][-1]['source'], 'shiji')

    def test_repeated_query_is_served_from_cache_without_quota(self):
        search = self.search(lambda r: httpx.Response(200, json=reply('a')), cache_dir=self.dir/'cache')
        first, second = search('同一句'), search('同一句')
        self.assertEqual((len(self.sent), self.spent()), (1, 1))
        self.assertEqual((first.cached, second.cached), (False, True))

    def test_cap_stops_before_the_request_is_sent(self):
        search = self.search(lambda r: httpx.Response(200, json=reply('a')), month_cap=1)
        search('一')
        with self.assertRaises(BudgetExceeded):
            search('二')
        self.assertEqual(len(self.sent), 1)

    def test_failures_never_echo_the_key_or_upstream_text(self):
        for response in [httpx.Response(401, text='bad key k-test'),
                         httpx.Response(200, json={'Response': {'Error': {'Code': 'AuthFailure', 'Message': 'k-test'}}})]:
            with self.subTest(status=response.status_code):
                with self.assertRaises(RuntimeError) as caught:
                    self.search(lambda r: response)('x')
                self.assertNotIn('k-test', str(caught.exception))

    def test_arrears_and_rate_limits_stop_as_budget_errors(self):
        for code in ['ResourceUnavailable', 'RequestLimitExceeded']:
            with self.subTest(code=code), self.assertRaises(BudgetExceeded):
                self.search(lambda r: httpx.Response(200, json={'Response': {'Error': {'Code': code}}}))('x')

    def test_missing_configuration_names_the_shiji_variables(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError) as caught:
            YuanbaoSearch()
        self.assertIn('SHIJI_WSA_KEY_FILE', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
