import unittest

import httpx

from shiji.links import check_url


class CheckUrlTests(unittest.TestCase):
    """Only pages that are really gone count as dead; anti-bot walls and timeouts stay unverified."""

    def check(self, handler, url='https://example.org/page'):
        return check_url(url, transport=httpx.MockTransport(handler))

    def test_gone_pages_are_dead(self):
        for status in (404, 410):
            with self.subTest(status=status):
                self.assertIs(self.check(lambda r, s=status: httpx.Response(s)), False)

    def test_working_pages_are_alive(self):
        self.assertIs(self.check(lambda r: httpx.Response(200)), True)

    def test_anti_bot_refusals_and_server_errors_stay_unverified(self):
        for status in (403, 412, 500):
            with self.subTest(status=status):
                self.assertIsNone(self.check(lambda r, s=status: httpx.Response(s)))

    def test_unknown_host_is_dead(self):
        def fail(request):
            raise httpx.ConnectError('[Errno 8] nodename nor servname provided, or not known', request=request)
        self.assertIs(self.check(fail), False)

    def test_certificate_problems_retry_over_plain_http(self):
        def handler(request):
            if request.url.scheme == 'https':
                raise httpx.ConnectError('[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed', request=request)
            return httpx.Response(200)
        self.assertIs(self.check(handler), True)

    def test_timeouts_stay_unverified(self):
        def slow(request):
            raise httpx.ReadTimeout('timed out', request=request)
        self.assertIsNone(self.check(slow))


if __name__ == '__main__':
    unittest.main()
