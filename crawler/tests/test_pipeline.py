import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crawler.models import Article, ConcernType, Sector, SkippedUrl, Source
from crawler.seed_data import BBC_BODY_XPATH
from crawler.services.discovery import Candidate, discover
from crawler.services.fetcher import FetchError, FetchResult, Refused, RobotsBlocked
from crawler.services.pipeline import crawl_source, extract_article, redact_personal, strip_repeated_title
from crawler.views import _csv_safe, _time_ago

STORY = (
    'Residents of Mbeya say water shortages have lasted for weeks and left households without supply. '
    'Traders complain that the shortage has raised prices, and farmers complain that their crops are dying. '
) * 4
PAID_MARKUP = '<script type="application/ld+json">{"@type": "NewsArticle", "isAccessibleForFree": "False"}</script>'


def page(body=STORY, head=''):
    html = f'<html><head><title>T</title>{head}</head><body><article><p>{body}</p></article></body></html>'
    return FetchResult(url='https://news.example/a', status=200, content=html.encode(), content_type='text/html', encoding='utf-8')


@override_settings(CRAWLER={**settings.CRAWLER, 'LANGUAGES': ['en', 'sw']})
class CrawlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_crawler', stdout=open('/dev/null', 'w'))
        cls.source = Source.objects.create(
            name='Test', url='https://news.example/feed', language='en', exclude_patterns='/sports/',
        )

    def crawl(self, candidates, fetcher=None, source=None):
        fetcher = fetcher or MagicMock(get=MagicMock(return_value=page()))
        with patch('crawler.services.pipeline.discover', return_value=candidates):
            return crawl_source(source or self.source, fetcher=fetcher), fetcher

    def test_concern_is_saved_with_sector_and_region(self):
        run, _ = self.crawl([Candidate('https://news.example/a', 'Mbeya residents complain of water shortage')])
        article = Article.objects.get()
        self.assertEqual((run.saved, run.flagged), (1, 1))
        self.assertTrue(article.is_concern)
        self.assertEqual(article.region, 'Mbeya')
        self.assertIn('water', article.sectors.values_list('slug', flat=True))

    def test_known_url_is_not_fetched_again(self):
        candidate = Candidate('https://news.example/a', 'Mbeya residents complain')
        self.crawl([candidate])
        _, fetcher = self.crawl([candidate])
        fetcher.get.assert_not_called()

    def test_same_text_at_another_url_is_a_duplicate(self):
        self.crawl([Candidate('https://news.example/a', 'Same headline')])
        run, _ = self.crawl([Candidate('https://other.example/b', 'Same headline')])
        self.assertEqual((run.saved, run.duplicates), (0, 1))

    def test_excluded_and_old_links_are_skipped_without_fetching(self):
        old = timezone.now() - timedelta(days=400)
        run, fetcher = self.crawl([
            Candidate('https://news.example/sports/x', 'Match report'),
            Candidate('https://news.example/old', 'Old story', published_at=old),
        ])
        fetcher.get.assert_not_called()
        self.assertEqual(run.skipped, 2)

    def test_limit_counts_only_new_articles(self):
        self.source.max_articles = 1
        self.source.save()
        fetcher = MagicMock(get=MagicMock(side_effect=[page('one ' + STORY), page('two ' + STORY)]))
        run, _ = self.crawl(
            [Candidate('https://news.example/1', 'One'), Candidate('https://news.example/2', 'Two')], fetcher,
        )
        self.assertEqual(run.saved, 1)
        self.assertEqual(fetcher.get.call_count, 1)

    def test_robots_block_is_counted_and_stored_nowhere(self):
        fetcher = MagicMock(get=MagicMock(side_effect=RobotsBlocked('no')))
        run, _ = self.crawl([Candidate('https://news.example/a', 'Title')], fetcher)
        self.assertEqual(run.blocked_by_robots, 1)
        self.assertEqual(Article.objects.count(), 0)

    def test_one_failed_article_does_not_stop_the_rest(self):
        fetcher = MagicMock(get=MagicMock(side_effect=[FetchError('boom'), page()]))
        run, _ = self.crawl(
            [Candidate('https://news.example/1', 'One'), Candidate('https://news.example/2', 'Two')], fetcher,
        )
        self.assertEqual((run.errors, run.saved), (1, 1))

    def test_paid_article_keeps_only_the_public_summary(self):
        fetcher = MagicMock(get=MagicMock(return_value=page(head=PAID_MARKUP)))
        self.crawl([Candidate('https://news.example/a', 'Water shortage', summary='A public summary of the story that is long enough.')], fetcher)
        article = Article.objects.get()
        self.assertTrue(article.is_paid_content)
        self.assertEqual(article.body, 'A public summary of the story that is long enough.')
        self.assertNotIn('Residents of Mbeya', article.body)

    def test_foreign_source_must_mention_tanzania(self):
        source = Source.objects.create(name='Global', url='https://g.example/', tanzania_focused=False)
        run, _ = self.crawl([Candidate('https://g.example/a', 'Kenyan residents complain')], source=source)
        self.assertEqual((run.saved, run.skipped), (0, 1))

    def test_rate_limited_source_is_not_retried(self):
        with patch('crawler.services.pipeline.discover', side_effect=Refused('slow down')) as discover:
            crawl_source(self.source, fetcher=MagicMock(sleep=MagicMock()))
        self.assertEqual(discover.call_count, 1)

    def test_short_feed_stub_is_not_used_as_summary(self):
        fetcher = MagicMock(get=MagicMock(return_value=page(head=PAID_MARKUP)))
        self.crawl([Candidate('https://news.example/a', 'Headline', summary='Soma hapa...')], fetcher)
        self.assertEqual(Article.objects.get().summary, '')

    def test_source_failure_is_recorded(self):
        with patch('crawler.services.pipeline.discover', side_effect=FetchError('feed down')):
            run = crawl_source(self.source, fetcher=MagicMock(sleep=MagicMock()))
        self.source.refresh_from_db()
        self.assertEqual(run.errors, 1)
        self.assertIn('feed down', self.source.last_error)


SWAHILI_STORY = (
    'Wananchi wa kijiji hicho wamesema kuwa barabara ni mbovu sana na wanalalamika kwa serikali. '
    'Wakazi wa wilaya hiyo wamesema kuwa uhaba wa maji ni changamoto kwa wananchi wote wa eneo hilo. '
) * 3


class RefreshTests(TestCase):
    LIVE_URL = 'https://news.example/live/1'

    @classmethod
    def setUpTestData(cls):
        call_command('seed_crawler', stdout=open('/dev/null', 'w'))
        cls.source = Source.objects.create(
            name='Live', url='https://news.example/feed', language='sw', refresh_pattern='/live/',
        )

    def crawl(self, body, url=None):
        fetcher = MagicMock(get=MagicMock(return_value=page(body)))
        with patch('crawler.services.pipeline.discover', return_value=[Candidate(url or self.LIVE_URL, 'Moja kwa moja')]):
            return crawl_source(self.source, fetcher=fetcher), fetcher

    def test_live_page_text_is_updated_and_review_is_kept(self):
        self.crawl(SWAHILI_STORY)
        Article.objects.update(review_status=Article.Review.CONFIRMED)
        run, _ = self.crawl(SWAHILI_STORY + ' Taarifa mpya imeongezwa kwenye ukurasa huu.')
        article = Article.objects.get()
        self.assertEqual((run.saved, run.refreshed), (0, 1))
        self.assertIn('Taarifa mpya', article.body)
        self.assertEqual(article.review_status, Article.Review.CONFIRMED)

    def test_unchanged_live_page_counts_as_no_update(self):
        self.crawl(SWAHILI_STORY)
        run, _ = self.crawl(SWAHILI_STORY)
        self.assertEqual(run.refreshed, 0)

    def test_failed_read_keeps_the_existing_text(self):
        self.crawl(SWAHILI_STORY)
        self.crawl('fupi')
        self.assertIn('Wananchi wa kijiji', Article.objects.get().body)

    def test_ordinary_story_is_not_fetched_again(self):
        self.crawl(SWAHILI_STORY, url='https://news.example/habari/1')
        _, fetcher = self.crawl(SWAHILI_STORY, url='https://news.example/habari/1')
        fetcher.get.assert_not_called()

    def test_radio_pages_are_excluded_from_bbc(self):
        bbc = Source.objects.get(name='BBC Swahili')
        fetcher = MagicMock()
        candidate = Candidate('https://www.bbc.com/swahili/bbc_swahili_radio/abc', 'Dira ya Dunia')
        with patch('crawler.services.pipeline.discover', return_value=[candidate]):
            run = crawl_source(bbc, fetcher=fetcher)
        fetcher.get.assert_not_called()
        self.assertEqual(run.skipped, 1)


class SwahiliOnlyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_crawler', stdout=open('/dev/null', 'w'))
        cls.source = Source.objects.create(name='Test', url='https://news.example/feed', language='sw')

    def crawl(self, body, url='https://news.example/a'):
        fetcher = MagicMock(get=MagicMock(return_value=page(body)))
        with patch('crawler.services.pipeline.discover', return_value=[Candidate(url, 'Kichwa cha habari')]):
            return crawl_source(self.source, fetcher=fetcher), fetcher

    def test_english_article_from_a_swahili_outlet_is_not_stored(self):
        run, _ = self.crawl(STORY)
        self.assertEqual((run.saved, run.skipped), (0, 1))
        self.assertEqual(Article.objects.count(), 0)

    def test_swahili_article_is_stored(self):
        run, _ = self.crawl(SWAHILI_STORY)
        self.assertEqual(run.saved, 1)
        self.assertEqual(Article.objects.get().language, 'sw')

    def test_rejected_link_is_not_fetched_again(self):
        self.crawl(STORY)
        self.assertTrue(SkippedUrl.objects.filter(url='https://news.example/a').exists())
        _, fetcher = self.crawl(STORY)
        fetcher.get.assert_not_called()


class ForumPrivacyTests(TestCase):
    POST_XPATH = '(//div[@class="post"])[1]'

    @classmethod
    def setUpTestData(cls):
        call_command('seed_crawler', stdout=open('/dev/null', 'w'))
        cls.source = Source.objects.create(
            name='Forum', url='https://forum.example/index.rss', language='sw', is_forum=True, body_xpath=cls.POST_XPATH,
        )

    def test_only_the_opening_post_is_kept_without_identities(self):
        html = (
            '<html><head><meta name="author" content="MemberName"></head><body>'
            f'<div class="post"><blockquote>Quoted Member said this</blockquote>{SWAHILI_STORY} '
            'Nipigie 0712 345 678 au mwandikie mtu@example.com, asante @rafiki</div>'
            '<div class="post">Second member reply</div></body></html>'
        )
        result = FetchResult('https://forum.example/t', 200, html.encode(), 'text/html', 'utf-8')
        fetcher = MagicMock(get=MagicMock(return_value=result))
        with patch('crawler.services.pipeline.discover', return_value=[Candidate('https://forum.example/t', 'Kero ya maji')]):
            crawl_source(self.source, fetcher=fetcher)
        article = Article.objects.get()
        self.assertEqual(article.author, '')
        for private in ('Quoted Member', 'Second member', '0712', 'mtu@example.com', '@rafiki', 'MemberName'):
            self.assertNotIn(private, article.body)
            self.assertNotIn(private, article.summary)

    def test_redaction_patterns(self):
        for text in ('+255 712345678', '255712345678', '0654-123-456', 'a.b@mail.co.tz', '@jina'):
            self.assertEqual(redact_personal(text), '[imeondolewa]', text)
        self.assertEqual(redact_personal('Sh 1,200,000 mwaka 2026'), 'Sh 1,200,000 mwaka 2026')


class ExtractionTests(TestCase):
    def test_repeated_headline_is_removed_from_the_text(self):
        self.assertEqual(strip_repeated_title('Kero ya maji!', 'Kero ya maji\nHabari kamili'), 'Habari kamili')
        self.assertEqual(strip_repeated_title('Kero ya maji', 'Habari kamili'), 'Habari kamili')

    def test_body_xpath_beats_a_longer_sidebar(self):
        html = (
            f'<html><body><div class="story">The real story.</div>'
            f'<article><p>{STORY}</p></article></body></html>'
        )
        content = extract_article(html, 'https://news.example/a', '//div[@class="story"]')
        self.assertEqual(content.body, 'The real story.')

    def test_paid_marker_in_graph_is_honoured(self):
        markup = json.dumps({'@graph': [{'@type': 'NewsArticle', 'isAccessibleForFree': False}]})
        html = f'<html><head><script type="application/ld+json">{markup}</script></head><body><p>{STORY}</p></body></html>'
        content = extract_article(html, 'https://news.example/a')
        self.assertTrue(content.is_paid)
        self.assertEqual(content.body, '')

    def test_empty_body_xpath_does_not_fall_back_to_the_extractor(self):
        html = f'<html><body><article><p>{STORY}</p></article></body></html>'
        self.assertEqual(extract_article(html, 'https://news.example/a', '//div[@class="story"]').body, '')

    def test_body_xpath_keeps_story_in_aside_and_drops_related_links(self):
        html = (
            '<html><body><div class="story"><aside>Residents spoke out.</aside>'
            '<div class="article-content-related">Also read this</div>'
            '<div class="nmgp content-page-ad_wrap">An advert</div></div></body></html>'
        )
        body = extract_article(html, 'https://news.example/a', '//div[@class="story"]').body
        self.assertEqual(body, 'Residents spoke out.')

    def test_line_wraps_inside_a_paragraph_stay_in_one_paragraph(self):
        html = '<html><body><div class="story"><p>Jeshi\nla Anga\nlimesema.</p><p>Aya ya pili.</p></div></body></html>'
        body = extract_article(html, 'https://news.example/a', '//div[@class="story"]').body
        self.assertEqual(body.splitlines(), ['Jeshi la Anga limesema.', 'Aya ya pili.'])

    def test_selected_paragraphs_stay_whole(self):
        html = '<html><body><p>Jeshi\nla Anga limesema.</p></body></html>'
        self.assertEqual(extract_article(html, 'https://news.example/a', '//p').body, 'Jeshi la Anga limesema.')

    def test_bbc_selection_reads_articles_and_live_posts_but_not_promos(self):
        article = (
            '<html><body><main><div><p>Aya ya habari.</p></div><h2>Kichwa kidogo</h2>'
            '<section><div><p>Tangazo la podcast</p></div></section><figure><div><p>Picha</p></div></figure>'
            '</main></body></html>'
        )
        live = (
            '<html><body><main><div><ol role="list"><li><article><h3>Chapisho</h3><div><p>Maandishi ya moja kwa moja.</p>'
            '<figure><div><p>Picha</p></div></figure></div></article></li></ol></div></main></body></html>'
        )
        self.assertEqual(
            extract_article(article, 'https://bbc.example/a', BBC_BODY_XPATH).body.splitlines(),
            ['Aya ya habari.', 'Kichwa kidogo'],
        )
        self.assertEqual(
            extract_article(live, 'https://bbc.example/l', BBC_BODY_XPATH).body.splitlines(),
            ['Chapisho', 'Maandishi ya moja kwa moja.'],
        )

    def test_body_xpath_rescues_text_the_extractor_misses(self):
        html = f'<html><body><div class="target"><script>var x=1;</script>{STORY}</div></body></html>'
        content = extract_article(html, 'https://news.example/a', '//div[@class="target"]')
        self.assertIn('Residents of Mbeya', content.body)
        self.assertNotIn('var x', content.body)


class GdeltTests(TestCase):
    def source(self):
        return Source(name='Search', kind='gdelt', url='https://api.gdeltproject.org/q')

    def fetcher(self, text):
        return MagicMock(get=MagicMock(return_value=FetchResult(
            url='u', status=200, content=text.encode(), content_type='application/json', encoding='utf-8',
        )))

    def test_articles_are_read_from_the_response(self):
        text = json.dumps({'articles': [
            {'url': 'https://a.example/x?utm_source=g', 'title': ' A  title ', 'seendate': '20261104T081500Z'},
            {'title': 'No link'},
        ]})
        candidates = discover(self.source(), self.fetcher(text))
        self.assertEqual([c.url for c in candidates], ['https://a.example/x'])
        self.assertEqual(candidates[0].title, 'A title')
        self.assertEqual(candidates[0].published_at.year, 2026)

    def test_empty_result_is_not_an_error(self):
        self.assertEqual(discover(self.source(), self.fetcher('{}')), [])

    def test_plain_text_rate_limit_message_counts_as_a_refusal(self):
        with self.assertRaises(Refused):
            discover(self.source(), self.fetcher('Please limit requests to one every 5 seconds'))


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = Source.objects.create(name='Test', url='https://news.example/feed')
        cls.energy = Sector.objects.create(slug='energy', name='Energy')
        cls.water = Sector.objects.create(slug='water', name='Water')
        make = lambda title, **kw: Article.objects.create(
            source=source, url=f'https://news.example/{title}', content_hash=title, title=title,
            published_at=kw.pop('published_at', timezone.now()), summary='Summary', language='sw',
            body='Aya ya kwanza.\nAya ya pili <b>muhimu</b>.', **kw,
        )
        cls.flagged = make('flagged', is_concern=True, concern_type=ConcernType.COMPLAINT, region='Mbeya')
        cls.flagged.sectors.add(cls.energy, through_defaults={'score': 5})
        make('routine')
        make('rejected', is_concern=True, review_status=Article.Review.REJECTED)
        make('confirmed', review_status=Article.Review.CONFIRMED)
        make('old', is_concern=True, published_at=timezone.now() - timedelta(days=60))
        cls.reader = get_user_model().objects.create_user('msomaji', password='siri-ndefu-sana-123')

    def setUp(self):
        self.client.force_login(self.reader)

    def titles(self, **params):
        response = self.client.get(reverse('dashboard'), params)
        self.assertEqual(response.status_code, 200)
        return {article.title for article in response.context['page'].object_list}

    def test_shows_flagged_and_confirmed_within_period(self):
        self.assertEqual(self.titles(), {'flagged', 'confirmed'})

    def test_period_filter(self):
        self.assertEqual(self.titles(period='all'), {'flagged', 'confirmed', 'old'})

    def test_filters(self):
        self.assertEqual(self.titles(sector='energy'), {'flagged'})
        self.assertEqual(self.titles(sector='none'), {'confirmed'})
        self.assertEqual(self.titles(region='Mbeya'), {'flagged'})
        self.assertEqual(self.titles(type='complaint'), {'flagged'})
        self.assertEqual(self.titles(q='confirm'), {'confirmed'})

    def test_headline_names_the_top_sectors_as_filter_links(self):
        response = self.client.get(reverse('dashboard'))
        summary = response.context['summary']
        self.assertEqual(summary['opening'], 'Siku 30 zilizopita')
        self.assertEqual([bar['slug'] for bar in summary['sectors']], ['energy'])
        self.assertContains(response, 'href="?period=30&amp;sector=energy"')

    def test_headline_with_a_sector_selected_or_nothing_found(self):
        summary = self.client.get(reverse('dashboard'), {'sector': 'energy'}).context['summary']
        self.assertEqual(summary['text'], 'kero 1 zilihusu')
        summary = self.client.get(reverse('dashboard'), {'q': 'hakuna-kitu'}).context['summary']
        self.assertEqual(summary['text'], 'hakuna kero zilizopatikana kwa vichujio hivi.')

    def test_type_counts_ignore_the_selected_type(self):
        rows = self.client.get(reverse('dashboard'), {'type': 'complaint'}).context['type_counts']
        by_type = {row['value']: row for row in rows}
        self.assertEqual(by_type['complaint']['total'], 1)
        self.assertTrue(by_type['complaint']['selected'])
        self.assertNotIn('type=', by_type['complaint']['query'])  # choosing it again clears it

    def test_each_filter_chip_removes_only_itself(self):
        chips = self.client.get(reverse('dashboard'), {'region': 'Mbeya', 'sector': 'energy'}).context['chips']
        self.assertEqual({chip['label']: chip['query'] for chip in chips}, {
            'Energy': 'region=Mbeya&period=30', 'Mbeya': 'sector=energy&period=30'})

    def test_bad_filter_values_do_not_crash(self):
        self.assertEqual(self.titles(period='nonsense', source='abc', page='zzz'), {'flagged', 'confirmed'})

    def test_sector_bars_ignore_the_selected_sector(self):
        bars = self.client.get(reverse('dashboard'), {'sector': 'water'}).context['sector_bars']
        totals = {bar['slug']: bar['total'] for bar in bars}
        self.assertEqual((totals['energy'], totals['water'], totals['none']), (1, 0, 1))

    def test_csv_honours_filters(self):
        content = self.client.get(reverse('export_csv'), {'sector': 'energy'}).content.decode()
        self.assertIn('flagged', content)
        self.assertNotIn('confirmed', content)

    def test_csv_neutralizes_formulas(self):
        self.assertEqual(_csv_safe('=HYPERLINK("x")'), '\'=HYPERLINK("x")')
        self.assertEqual(_csv_safe('Plain'), 'Plain')

    def test_scraped_html_is_escaped(self):
        Article.objects.filter(pk=self.flagged.pk).update(title='<script>alert(1)</script>')
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('<script>alert(1)', html)

    def test_other_languages_are_hidden(self):
        Article.objects.filter(pk=self.flagged.pk).update(language='en')
        self.assertEqual(self.titles(), {'confirmed'})

    def test_story_page_renders_text_without_leaving_the_site(self):
        html = self.client.get(reverse('article', args=[self.flagged.pk])).content.decode()
        self.assertIn('Aya ya kwanza.', html)
        self.assertIn('Aya ya pili &lt;b&gt;', html)
        self.assertNotIn('https://news.example/', html)

    def test_each_line_of_text_is_its_own_paragraph(self):
        html = self.client.get(reverse('article', args=[self.flagged.pk])).content.decode()
        self.assertIn('<p>Aya ya kwanza.</p>', html)

    def test_extractor_list_markers_are_not_shown(self):
        self.assertEqual(Article(body='- Kipengele\n\nAya').paragraphs, ['Kipengele', 'Aya'])

    def test_list_shows_the_whole_text_uncut(self):
        long_text = 'Sentensi ndefu ya habari. ' * 100
        Article.objects.filter(pk=self.flagged.pk).update(body=long_text, summary='Kifupi…')
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn(long_text.strip(), html)
        self.assertNotIn('Kifupi…', html)

    def test_story_with_no_text_is_not_listed(self):
        Article.objects.filter(pk=self.flagged.pk).update(body='')
        self.assertEqual(self.titles(), {'confirmed'})

    def test_readers_do_not_see_the_classifier_panel(self):
        html = self.client.get(reverse('article', args=[self.flagged.pk])).content.decode()
        self.assertNotIn('Kwa nini habari hii', html)

    def test_time_since_crawl_is_in_swahili(self):
        now = timezone.now()
        self.assertEqual(_time_ago(now), 'sasa hivi')
        self.assertEqual(_time_ago(now - timedelta(minutes=5)), 'dakika 5 zilizopita')
        self.assertEqual(_time_ago(now - timedelta(hours=3)), 'saa 3 zilizopita')
        self.assertEqual(_time_ago(now - timedelta(days=2)), 'siku 2 zilizopita')

    def test_dashboard_and_csv_do_not_expose_source_links(self):
        self.assertNotIn('https://news.example/', self.client.get(reverse('dashboard')).content.decode())
        self.assertNotIn('https://news.example/', self.client.get(reverse('export_csv')).content.decode())

    def test_forum_post_is_labelled_unverified(self):
        forum = Source.objects.create(name='Forum', url='https://f.example/', is_forum=True)
        Article.objects.filter(pk=self.flagged.pk).update(source=forum)
        html = self.client.get(reverse('article', args=[self.flagged.pk])).content.decode()
        self.assertIn('halijathibitishwa', html)

    def test_review_requires_staff(self):
        url = reverse('review_article', args=[self.flagged.pk])
        self.client.post(url, {'decision': 'rejected'})
        self.flagged.refresh_from_db()
        self.assertEqual(self.flagged.review_status, 'unreviewed')

    def test_staff_rejection_hides_story(self):
        self.client.force_login(get_user_model().objects.create_user('editor', password='x', is_staff=True))
        self.client.post(reverse('review_article', args=[self.flagged.pk]), {'decision': 'rejected'})
        self.assertEqual(self.titles(), {'confirmed'})

    def test_empty_period_points_to_older_concerns(self):
        Article.objects.filter(published_at__gte=timezone.now() - timedelta(days=30)).delete()
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['outside_period'], 1)
        self.assertEqual(response.context['summary']['text'], 'hakuna kero mpya zilizopatikana.')
        self.assertContains(response, 'href="?period=all"')
        self.assertNotContains(response, 'Bado hakuna kero zilizopatikana')

    def test_region_choices_follow_the_other_filters(self):
        regions = self.client.get(reverse('dashboard'), {'sector': 'none'}).context['regions']
        self.assertEqual(regions, [])
        regions = self.client.get(reverse('dashboard')).context['regions']
        self.assertEqual(regions, [{'value': 'Mbeya', 'total': 1}])

    def test_sort_by_score(self):
        Article.objects.filter(pk=self.flagged.pk).update(concern_score=1)
        Article.objects.filter(title='confirmed').update(concern_score=9)
        page = self.client.get(reverse('dashboard'), {'order': 'score'}).context['page']
        self.assertEqual([a.title for a in page.object_list], ['confirmed', 'flagged'])

    def test_selected_sector_in_headline_is_not_a_toggle_link(self):
        response = self.client.get(reverse('dashboard'), {'sector': 'energy'})
        self.assertNotContains(response, 'title="Ondoa kichujio cha sekta hii"')
        self.assertEqual(response.context['summary']['selected_sector'], 'Energy')

    def test_story_headings_are_outside_the_toggle_button(self):
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('<h3><button type="button" class="story-toggle"', html)
        self.assertNotIn('<summary>', html)

    def test_story_link_remembers_the_list_it_came_from(self):
        html = self.client.get(reverse('dashboard'), {'region': 'Mbeya'}).content.decode()
        self.assertIn('?back=region%3DMbeya%26period%3D30', html)
        page = self.client.get(reverse('article', args=[self.flagged.pk]), {'back': 'region=Mbeya&period=30'})
        self.assertEqual(page.context['back_url'], '/?region=Mbeya&period=30')

    def test_readers_cannot_open_rejected_or_unflagged_stories(self):
        for title in ('rejected', 'routine'):
            pk = Article.objects.get(title=title).pk
            self.assertEqual(self.client.get(reverse('article', args=[pk])).status_code, 404)

    def test_csv_starts_with_a_byte_order_mark_for_excel(self):
        self.assertTrue(self.client.get(reverse('export_csv')).content.startswith('﻿'.encode()))

    def test_review_confirms_with_a_message_and_can_move_on(self):
        self.client.force_login(get_user_model().objects.create_user('editor', password='x', is_staff=True))
        page = self.client.get(reverse('article', args=[self.flagged.pk]))
        self.assertEqual(page.context['next_unreviewed'].title, 'old')
        response = self.client.post(reverse('review_article', args=[self.flagged.pk]),
                                    {'decision': 'confirmed', 'then': 'next'}, follow=True)
        self.assertEqual(response.context['article'].title, 'old')
        self.assertContains(response, '“flagged” imethibitishwa')
        html = self.client.get(reverse('article', args=[self.flagged.pk])).content.decode()
        self.assertNotIn('value="confirmed"', html)  # already confirmed: no button to confirm again
        self.assertIn('value="unreviewed"', html)
