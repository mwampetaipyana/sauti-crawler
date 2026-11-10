"""The crawl: discover links, fetch each article once, extract its text, classify and store it."""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone

import trafilatura
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from lxml import etree, html as lxml_html

from crawler.models import Article, ArticleSector, CrawlRun, Sector, SkippedUrl

from .classifier import Lexicon, classify, mentions_tanzania
from .discovery import discover
from .fetcher import FetchError, HostRefused, PoliteFetcher, Refused, RobotsBlocked

logger = logging.getLogger(__name__)

SUMMARY_CHARS = 320
MIN_SUMMARY_CHARS = 40  # Shorter feed text is usually a stub such as "Soma hapa...".
MAX_ERROR_LINES = 20
REDACTED = '[imeondolewa]'
_TEXT_BLOCK_TAGS = ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li')
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PHONE = re.compile(r'(?<![\d.])(?:\+?255|0)[\s-]?[67]\d(?:[\s-]?\d){7}(?!\d)')
_MENTION = re.compile(r'(?<![\w@])@\w+')
_NON_STORY_XPATH = (
    './/script|.//style|.//noscript|.//figure'
    '|.//*[contains(@class, "related") or contains(@class, "ad_wrap")]'
)
DISCOVERY_ATTEMPTS = 3
DISCOVERY_RETRY_SECONDS = 10


@dataclass
class PageContent:
    title: str = ''
    body: str = ''
    author: str = ''
    published_at: datetime | None = None
    is_paid: bool = False


def content_hash(title, body):
    """Hash normalized text so a story syndicated across sites is stored once."""
    text = re.sub(r'\W+', ' ', f'{title} {body[:2000]}'.lower()).strip()
    return hashlib.sha256(text.encode()).hexdigest()


def excerpt(text, limit=SUMMARY_CHARS):
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(' ', 1)[0] + '…'


def is_marked_paid(tree):
    """True when the page's structured data says the publisher charges for this article."""
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(script.text_content())
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        items += [node for item in items if isinstance(item, dict) for node in item.get('@graph', [])]
        for item in items:
            if isinstance(item, dict) and str(item.get('isAccessibleForFree', '')).lower() == 'false':
                return True
    return False


def redact_personal(text):
    """Remove handles and contact details, which identify forum members and others they mention."""
    text = _EMAIL.sub(REDACTED, text)
    text = _PHONE.sub(REDACTED, text)
    return _MENTION.sub(REDACTED, text)


def strip_repeated_title(title, body):
    """Some sites start the text with the headline, which the reader page already shows."""
    first, _, rest = body.partition('\n')
    return rest.lstrip() if _comparable(first) == _comparable(title) else body


def _comparable(text):
    return re.sub(r'\W+', ' ', text).strip().lower()


def _join_wrapped_lines(node):
    """Line breaks inside a paragraph's HTML are not paragraph breaks, so keep each one whole."""
    for block in node.iter(*_TEXT_BLOCK_TAGS):
        tail = block.tail
        text = ' '.join(block.text_content().split())
        block.clear()
        block.text = f'{text}\n'
        block.tail = tail


def _text_at_xpath(tree, xpath, drop_quotes=False):
    remove = _NON_STORY_XPATH + ('|.//blockquote|.//*[contains(@class, "bbCodeBlock")]' if drop_quotes else '')
    blocks = []
    for node in tree.xpath(xpath):
        # Not <aside>: The Citizen and Mwananchi put every story paragraph inside one.
        for child in node.xpath(remove):
            child.drop_tree()
        _join_wrapped_lines(node)
        text = re.sub(r'[ \t]*\n\s*', '\n', node.text_content()).strip()
        if text:
            blocks.append(text)
    return '\n'.join(blocks)


def extract_article(html, url, body_xpath='', is_forum=False):
    """Read a page into a PageContent. Paid articles keep their metadata but no text."""
    document = trafilatura.bare_extraction(
        html, url=url, with_metadata=True, include_comments=False, include_tables=False,
    )
    page = PageContent()
    if document is not None:
        page.title = document.title or ''
        page.body = document.text or ''
        page.author = document.author or ''
        page.published_at = _parse_page_date(document.date)

    try:
        tree = lxml_html.fromstring(html)
    except (ValueError, etree.ParserError):
        return page

    if is_marked_paid(tree):
        page.is_paid = True
        page.body = ''
    elif body_xpath:
        # On these sites the extractor can pick up a sidebar of other headlines instead of the
        # story. Trust only the configured location, falling back to the feed summary if empty.
        page.body = _text_at_xpath(tree, body_xpath, drop_quotes=is_forum)
    return page


def _parse_page_date(value):
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').replace(tzinfo=dt_timezone.utc)
    except ValueError:
        return None


def apply_classification(article, result, sector_ids):
    """Store a classifier result. Reviewer decisions are kept."""
    article.is_concern = result.is_concern
    article.concern_score = result.concern_score
    article.concern_type = result.concern_type
    article.region = result.region
    article.matched_terms = result.matched_terms
    # The text itself decides: Swahili outlets sometimes publish in English.
    article.language = result.language or article.source.language
    with transaction.atomic():
        article.save()
        ArticleSector.objects.filter(article=article).delete()
        ArticleSector.objects.bulk_create([
            ArticleSector(article=article, sector_id=sector_ids[slug], score=score)
            for slug, score in result.sector_scores.items()
            if slug in sector_ids
        ])


class SourceCrawl:
    def __init__(self, source, fetcher=None, lexicon=None, limit=None, sector_ids=None):
        self.source = source
        self.fetcher = fetcher or PoliteFetcher()
        self.fetcher.min_delay = source.min_delay_seconds
        self.lexicon = lexicon or Lexicon.load()
        self.limit = limit if limit is not None else source.max_articles
        self.sector_ids = sector_ids or dict(Sector.objects.values_list('slug', 'id'))
        self.excludes = [re.compile(line.strip()) for line in source.exclude_patterns.splitlines() if line.strip()]
        self.oldest = timezone.now() - timedelta(days=settings.CRAWLER['MAX_ARTICLE_AGE_DAYS'])
        self.min_body = settings.CRAWLER['MIN_BODY_CHARS']
        self.run = CrawlRun(source=source)
        self._error_lines = []

    def execute(self):
        self.run.save()
        try:
            candidates = self._discover_with_retries()
            self.run.discovered = len(candidates)
            self._process(candidates)
        except FetchError as exc:
            self._record_error(f'Could not read source: {exc}')

        self.run.finished_at = timezone.now()
        self.run.error_detail = '\n'.join(self._error_lines)
        self.run.save()
        self.source.last_crawled_at = self.run.finished_at
        self.source.last_error = self._error_lines[-1] if self._error_lines else ''
        self.source.save(update_fields=['last_crawled_at', 'last_error'])
        return self.run

    def _discover_with_retries(self):
        """Some feeds and DNS lookups fail intermittently, and one miss would waste a whole run."""
        for attempt in range(1, DISCOVERY_ATTEMPTS + 1):
            try:
                return discover(self.source, self.fetcher)
            except (RobotsBlocked, Refused):
                raise  # Asking again would ignore what the site just told us.
            except FetchError as exc:
                if attempt == DISCOVERY_ATTEMPTS:
                    raise
                logger.info('%s: attempt %d failed: %s', self.source.name, attempt, exc)
                self.fetcher.sleep(DISCOVERY_RETRY_SECONDS)

    def _process(self, candidates):
        urls = [c.url for c in candidates]
        known = set(Article.objects.filter(url__in=urls).values_list('url', flat=True))
        known |= set(SkippedUrl.objects.filter(url__in=urls).values_list('url', flat=True))
        fetched = 0
        for candidate in candidates:
            is_known = candidate.url in known
            if is_known and not self._should_refresh(candidate):
                continue
            if not is_known:
                if fetched >= self.limit:
                    continue
                if self._is_excluded(candidate) or self._is_too_old(candidate.published_at):
                    self.run.skipped += 1
                    continue
                fetched += 1
            try:
                if is_known:
                    self._refresh_candidate(candidate)
                else:
                    self._process_candidate(candidate)
            except RobotsBlocked as exc:
                self.run.blocked_by_robots += 1
                logger.info('%s', exc)
            except HostRefused as exc:
                self._record_error(str(exc))
                if self.source.kind != 'gdelt':
                    break  # Every remaining link is on this host.
            except FetchError as exc:
                self._record_error(str(exc))

    def _should_refresh(self, candidate):
        pattern = self.source.refresh_pattern
        return bool(pattern) and bool(re.search(pattern, candidate.url))

    def _refresh_candidate(self, candidate):
        """Update a changing page in place. Reviewer decisions are kept."""
        article = Article.objects.filter(url=candidate.url).select_related('source').first()
        if article is None:
            return  # Rejected earlier, so there is nothing to update.
        page = self.fetcher.get(candidate.url)
        content = extract_article(page.text, page.url, self.source.body_xpath, is_forum=self.source.is_forum)
        title = candidate.title or article.title
        body = strip_repeated_title(title, content.body)
        if content.is_paid or len(body) < self.min_body:
            return
        digest = content_hash(title, body)
        if digest == article.content_hash:
            return
        article.title, article.body, article.content_hash = title[:500], body, digest
        article.summary = excerpt(body)
        article.body_is_partial = False
        apply_classification(article, classify(title, body, self.lexicon), self.sector_ids)
        self.run.refreshed += 1

    def _is_excluded(self, candidate):
        return any(pattern.search(candidate.url) for pattern in self.excludes)

    def _is_too_old(self, published_at):
        return published_at is not None and published_at < self.oldest

    def _reject(self, candidate):
        self.run.skipped += 1
        SkippedUrl.objects.get_or_create(url=candidate.url)

    def _process_candidate(self, candidate):
        page = self.fetcher.get(candidate.url)
        is_forum = self.source.is_forum
        content = extract_article(page.text, page.url, self.source.body_xpath, is_forum=is_forum)
        title = candidate.title or content.title
        body = content.body
        summary = candidate.summary if len(candidate.summary) >= MIN_SUMMARY_CHARS else ''
        if is_forum:
            title, body, summary = redact_personal(title), redact_personal(body), ''
        body = strip_repeated_title(title, body)
        body_is_partial = not is_forum and len(body) < self.min_body
        if body_is_partial:
            # A short feed summary is still worth keeping when the page text cannot be used.
            body = max(body, summary, key=len)
        if not title or (len(body) < MIN_SUMMARY_CHARS and not content.is_paid):
            self._reject(candidate)
            return

        published_at = candidate.published_at or content.published_at
        if self._is_too_old(published_at):
            self._reject(candidate)
            return
        if not self.source.tanzania_focused and not mentions_tanzania(title, body):
            self._reject(candidate)
            return

        result = classify(title, body, self.lexicon)
        if (result.language or self.source.language) not in settings.CRAWLER['LANGUAGES']:
            self._reject(candidate)
            return

        digest = content_hash(title, body)
        if Article.objects.filter(content_hash=digest).exists():
            self.run.duplicates += 1
            SkippedUrl.objects.get_or_create(url=candidate.url)
            return

        article = Article(
            source=self.source,
            url=candidate.url,
            content_hash=digest,
            title=title[:500],
            body=body,
            summary=excerpt(summary or body),
            author='' if is_forum else content.author[:200],
            language=self.source.language,
            body_is_partial=body_is_partial,
            is_paid_content=content.is_paid,
            published_at=published_at or timezone.now(),
            published_is_estimated=published_at is None,
        )
        try:
            apply_classification(article, result, self.sector_ids)
        except IntegrityError:
            self.run.duplicates += 1  # Another crawl stored this URL first.
            return
        self.run.saved += 1
        self.run.flagged += int(result.is_concern)

    def _record_error(self, message):
        self.run.errors += 1
        if len(self._error_lines) < MAX_ERROR_LINES:
            self._error_lines.append(message)
        logger.warning('%s: %s', self.source.name, message)


def crawl_source(source, **kwargs):
    return SourceCrawl(source, **kwargs).execute()
