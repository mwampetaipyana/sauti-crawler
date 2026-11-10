from unittest.mock import MagicMock

from django.test import SimpleTestCase

from crawler.services.discovery import normalize_url
from crawler.services.fetcher import FetchError, HostRefused, PoliteFetcher, RobotsBlocked, is_public_url


def response(status=200, body=b'', headers=None):
    fake = MagicMock()
    fake.status_code = status
    fake.text = body.decode()
    fake.headers = headers or {}
    fake.encoding = 'utf-8'
    fake.iter_content.return_value = [body]
    fake.__enter__.return_value = fake
    return fake


def fetcher_for(routes):
    """Build a fetcher whose session answers from {url: response or list of responses}."""
    session = MagicMock()
    session.headers = {}
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        answer = routes.get(url, response(404))
        return answer.pop(0) if isinstance(answer, list) else answer

    session.get.side_effect = get
    sleeps = []
    fetcher = PoliteFetcher(min_delay=0, session=session, sleep=sleeps.append, url_check=lambda url: True)
    fetcher.calls, fetcher.sleeps = calls, sleeps
    return fetcher


ROBOTS = 'https://news.example/robots.txt'
PAGE = 'https://news.example/story'


class RobotsTests(SimpleTestCase):
    def test_disallowed_url_is_never_requested(self):
        fetcher = fetcher_for({ROBOTS: response(body=b'User-agent: *\nDisallow: /story\n')})
        with self.assertRaises(RobotsBlocked):
            fetcher.get(PAGE)
        self.assertEqual(fetcher.calls, [ROBOTS])

    def test_rule_for_our_bot_name_applies(self):
        rules = b'User-agent: SautiCrawler\nDisallow: /\n\nUser-agent: *\nAllow: /\n'
        fetcher = fetcher_for({ROBOTS: response(body=rules)})
        with self.assertRaises(RobotsBlocked):
            fetcher.get(PAGE)

    def test_missing_robots_file_allows_crawling(self):
        fetcher = fetcher_for({ROBOTS: response(404), PAGE: response(body=b'hello')})
        self.assertEqual(fetcher.get(PAGE).content, b'hello')

    def test_robots_server_error_blocks_host(self):
        fetcher = fetcher_for({ROBOTS: response(500), PAGE: response(body=b'hello')})
        with self.assertRaises(RobotsBlocked):
            fetcher.get(PAGE)

    def test_redirect_target_is_checked_against_robots(self):
        other_robots = 'https://other.example/robots.txt'
        fetcher = fetcher_for({
            ROBOTS: response(404),
            PAGE: response(302, headers={'Location': 'https://other.example/private'}),
            other_robots: response(body=b'User-agent: *\nDisallow: /private\n'),
        })
        with self.assertRaises(RobotsBlocked):
            fetcher.get(PAGE)
        self.assertNotIn('https://other.example/private', fetcher.calls)

    def test_robots_is_read_once_per_host(self):
        fetcher = fetcher_for({ROBOTS: response(404), PAGE: response(body=b'x')})
        fetcher.get(PAGE)
        fetcher.get(PAGE)
        self.assertEqual(fetcher.calls.count(ROBOTS), 1)


class BackoffTests(SimpleTestCase):
    def test_retry_after_is_honoured_once(self):
        fetcher = fetcher_for({
            ROBOTS: response(404),
            PAGE: [response(429, headers={'Retry-After': '30'}), response(body=b'ok')],
        })
        self.assertEqual(fetcher.get(PAGE).content, b'ok')
        self.assertIn(30, fetcher.sleeps)

    def test_long_retry_after_is_not_waited_for(self):
        fetcher = fetcher_for({ROBOTS: response(404), PAGE: response(429, headers={'Retry-After': '9999'})})
        with self.assertRaises(FetchError):
            fetcher.get(PAGE)
        self.assertNotIn(9999, fetcher.sleeps)

    def test_host_is_left_alone_after_repeated_refusals(self):
        fetcher = fetcher_for({ROBOTS: response(404), PAGE: response(403)})
        for _ in range(3):
            with self.assertRaises(FetchError):
                fetcher.get(PAGE)
        before = fetcher.calls.count(PAGE)
        with self.assertRaises(HostRefused):
            fetcher.get(PAGE)
        self.assertEqual(fetcher.calls.count(PAGE), before)

    def test_one_rate_limit_stops_requests_to_that_host(self):
        fetcher = fetcher_for({ROBOTS: response(404), PAGE: response(429)})
        with self.assertRaises(FetchError):
            fetcher.get(PAGE)
        with self.assertRaises(HostRefused):
            fetcher.get('https://news.example/another')
        self.assertEqual(fetcher.calls, [ROBOTS, PAGE])

    def test_error_message_leaves_out_the_query_string(self):
        url = PAGE + '?query=very-long'
        fetcher = fetcher_for({ROBOTS: response(404), url: response(403)})
        with self.assertRaises(FetchError) as caught:
            fetcher.get(url)
        self.assertNotIn('very-long', str(caught.exception))

    def test_oversized_response_is_rejected(self):
        fetcher = fetcher_for({ROBOTS: response(404), PAGE: response(body=b'x' * 100)})
        fetcher.max_bytes = 10
        with self.assertRaises(FetchError):
            fetcher.get(PAGE)


class UrlTests(SimpleTestCase):
    def test_private_addresses_are_rejected(self):
        for url in ('http://127.0.0.1/', 'http://10.0.0.1/', 'http://169.254.169.254/latest', 'file:///etc/passwd'):
            self.assertFalse(is_public_url(url), url)

    def test_tracking_parameters_are_removed(self):
        self.assertEqual(
            normalize_url('https://News.example/a?id=1&utm_source=x&fbclid=y#top'),
            'https://news.example/a?id=1',
        )
