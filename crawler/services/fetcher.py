"""HTTP client that follows the rules: robots.txt, per-host pacing, and backing off when refused."""
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from django.conf import settings
from protego import Protego

logger = logging.getLogger(__name__)

REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
MAX_RETRY_WAIT_SECONDS = 120
REFUSAL_CODES = {401, 403, 429, 451, 503}


class FetchError(Exception):
    pass


class RobotsBlocked(FetchError):
    pass


class Refused(FetchError):
    """The server said no or told us to slow down, so retrying soon would be impolite."""


class HostRefused(Refused):
    """The host has refused repeatedly, so it is left alone for the rest of this run."""


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str
    encoding: str | None

    @property
    def text(self):
        try:
            return self.content.decode(self.encoding or 'utf-8', errors='replace')
        except LookupError:
            return self.content.decode('utf-8', errors='replace')


def _short(url):
    """A URL without its query string, which keeps log lines readable."""
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}{parts.path}'


def is_public_url(url):
    """Reject non-web schemes and hosts on private networks, since URLs come from third parties."""
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return False
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f'Could not look up {parts.hostname}: {exc}') from exc
    return all(ipaddress.ip_address(a[4][0].split('%')[0]).is_global for a in addresses)


class PoliteFetcher:
    def __init__(self, min_delay=3.0, session=None, sleep=time.sleep, url_check=is_public_url):
        config = settings.CRAWLER
        self.bot_name = config['BOT_NAME']
        contact = config['CONTACT_URL']
        self.user_agent = f'{self.bot_name}/0.1' + (f' (+{contact})' if contact else '')
        self.timeout = config['TIMEOUT_SECONDS']
        self.max_bytes = config['MAX_RESPONSE_BYTES']
        self.refusal_limit = config['HOST_REFUSAL_LIMIT']
        self.min_delay = min_delay
        self.session = session or requests.Session()
        self.session.headers['User-Agent'] = self.user_agent
        self.sleep = sleep
        self.url_check = url_check
        self._robots = {}
        self._last_request = {}
        self._refusals = {}

    def get(self, url):
        """Fetch a URL, checking robots.txt on every redirect hop."""
        for _ in range(MAX_REDIRECTS + 1):
            self._require_allowed(url)
            response = self._request(url)
            if response.status_code in REDIRECT_CODES and response.headers.get('Location'):
                response.close()
                url = urljoin(url, response.headers['Location'])
                continue
            return self._read(url, response)
        raise FetchError(f'Too many redirects: {url}')

    def can_fetch(self, url):
        robots = self._robots_for(url)
        return robots is not None and robots.can_fetch(url, self.bot_name)

    def _require_allowed(self, url):
        if not self.url_check(url):
            raise FetchError(f'Not a public web address: {url}')
        host = urlsplit(url).netloc.lower()
        if self._refusals.get(host, 0) >= self.refusal_limit:
            raise HostRefused(f'{host} refused repeated requests; skipping it this run')
        if not self.can_fetch(url):
            raise RobotsBlocked(f'robots.txt disallows {url}')

    def _robots_for(self, url):
        parts = urlsplit(url)
        origin = f'{parts.scheme}://{parts.netloc.lower()}'
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin]

    def _load_robots(self, origin):
        """A missing robots.txt allows everything. An unreadable one blocks the host this run."""
        host = urlsplit(origin).netloc
        self._wait_for_host(host)
        try:
            response = self.session.get(f'{origin}/robots.txt', timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning('Could not read robots.txt from %s: %s', origin, exc)
            return None
        if response.status_code == 200:
            return Protego.parse(response.text)
        if 400 <= response.status_code < 500 and response.status_code not in (401, 403, 429):
            return Protego.parse('')
        logger.warning('robots.txt from %s returned %s', origin, response.status_code)
        return None

    def _wait_for_host(self, host, delay=None):
        delay = self.min_delay if delay is None else delay
        last = self._last_request.get(host)
        if last is not None:
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def _delay_for(self, url):
        robots = self._robots_for(url)
        robots_delay = robots.crawl_delay(self.bot_name) if robots else None
        return max(self.min_delay, robots_delay or 0)

    def _request(self, url):
        host = urlsplit(url).netloc.lower()
        for attempt in range(2):
            self._wait_for_host(host, self._delay_for(url))
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=False, stream=True)
            except requests.RequestException as exc:
                raise FetchError(f'{_short(url)}: {exc}') from exc

            if response.status_code not in REFUSAL_CODES:
                self._refusals[host] = 0
                return response

            retry_wait = self._retry_wait(response)
            response.close()
            if attempt == 0 and retry_wait is not None:
                logger.info('%s asked us to wait %ss', host, retry_wait)
                self.sleep(retry_wait)
                continue
            # A 429 is an explicit request to stop, so the host gets no second attempt this run.
            refusals = self.refusal_limit if response.status_code == 429 else self._refusals.get(host, 0) + 1
            self._refusals[host] = refusals
            error = FetchError if response.status_code == 503 else Refused
            raise error(f'{_short(url)}: HTTP {response.status_code}')
        raise FetchError(f'{_short(url)}: gave up')

    @staticmethod
    def _retry_wait(response):
        """Retry only rate limits and outages, and only for a wait the server itself asked for."""
        if response.status_code not in (429, 503):
            return None
        value = response.headers.get('Retry-After', '')
        if value.isdigit() and int(value) <= MAX_RETRY_WAIT_SECONDS:
            return int(value)
        return None

    def _read(self, url, response):
        with response:
            if response.status_code >= 400:
                raise FetchError(f'{url}: HTTP {response.status_code}')
            content = bytearray()
            try:
                for chunk in response.iter_content(64 * 1024):
                    content.extend(chunk)
                    if len(content) > self.max_bytes:
                        raise FetchError(f'{url}: response larger than {self.max_bytes} bytes')
            except requests.RequestException as exc:
                raise FetchError(f'{url}: {exc}') from exc
            return FetchResult(
                url=url,
                status=response.status_code,
                content=bytes(content),
                content_type=response.headers.get('Content-Type', ''),
                # Without a declared charset, requests assumes Latin-1 and garbles UTF-8 pages.
                encoding=response.encoding if 'charset' in response.headers.get('Content-Type', '').lower() else None,
            )
