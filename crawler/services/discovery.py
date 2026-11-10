"""Turn a Source into a list of candidate article links."""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
from lxml import html as lxml_html

from .fetcher import FetchError, Refused

TRACKING_PARAMS = re.compile(r'^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|igshid)$', re.IGNORECASE)


@dataclass
class Candidate:
    url: str
    title: str = ''
    summary: str = ''
    published_at: datetime | None = None


def normalize_url(url):
    """Drop fragments and tracking parameters so one article has one URL."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ''))


def discover(source, fetcher):
    handlers = {'rss': discover_feed, 'html': discover_listing, 'gdelt': discover_gdelt}
    candidates, seen = [], set()
    for candidate in handlers[source.kind](source, fetcher):
        candidate.url = normalize_url(candidate.url)
        if candidate.url not in seen and urlsplit(candidate.url).scheme in ('http', 'https'):
            seen.add(candidate.url)
            candidates.append(candidate)
    return candidates


def discover_feed(source, fetcher):
    parsed = feedparser.parse(fetcher.get(source.url).content)
    if parsed.bozo and not parsed.entries:
        raise FetchError(f'{source.url}: not a readable feed ({parsed.get("bozo_exception")})')
    return [
        Candidate(
            url=entry.link,
            title=_clean(entry.get('title', '')),
            summary=_strip_tags(entry.get('summary', '')),
            published_at=_feed_date(entry),
        )
        for entry in parsed.entries
        if entry.get('link')
    ]


def discover_listing(source, fetcher):
    if not source.include_pattern:
        raise FetchError(f'{source.name}: HTML sources need an include pattern')
    include = re.compile(source.include_pattern)
    result = fetcher.get(source.url)
    tree = lxml_html.fromstring(result.content, base_url=result.url)
    host = urlsplit(result.url).netloc
    candidates = []
    for link in tree.xpath('//a[@href]'):
        url = urljoin(result.url, link.get('href'))
        if urlsplit(url).netloc == host and include.search(url):
            candidates.append(Candidate(url=url, title=_clean(link.text_content())))
    return candidates


def discover_gdelt(source, fetcher):
    result = fetcher.get(source.url)
    text = result.text.strip()
    # GDELT answers rate limits and query errors with a plain-text sentence and HTTP 200.
    if not text.startswith('{'):
        raise Refused(f'GDELT refused the query: {text[:160]}')
    try:
        articles = json.loads(text).get('articles', [])
    except ValueError as exc:
        raise FetchError(f'GDELT returned invalid JSON: {exc}') from exc
    return [
        Candidate(url=item['url'], title=_clean(item.get('title', '')), published_at=_gdelt_date(item.get('seendate')))
        for item in articles
        if item.get('url')
    ]


def _feed_date(entry):
    parsed = entry.get('published_parsed') or entry.get('updated_parsed')
    return datetime(*parsed[:6], tzinfo=dt_timezone.utc) if parsed else None


def _gdelt_date(value):
    try:
        return datetime.strptime(value or '', '%Y%m%dT%H%M%SZ').replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None


def _strip_tags(value):
    if not value.strip():
        return ''
    try:
        return _clean(lxml_html.fromstring(f'<div>{value}</div>').text_content())
    except (ValueError, lxml_html.etree.ParserError):
        return _clean(value)


def _clean(value):
    return re.sub(r'\s+', ' ', value).strip()
