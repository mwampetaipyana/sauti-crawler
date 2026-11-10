from django.db import models
from django.db.models import Q
from django.utils import timezone


class Source(models.Model):
    class Kind(models.TextChoices):
        RSS = 'rss', 'Mlisho wa RSS au Atom'
        HTML = 'html', 'Ukurasa wa orodha (HTML)'
        GDELT = 'gdelt', 'Utafutaji wa habari wa GDELT'

    name = models.CharField(verbose_name='jina', max_length=120, unique=True)
    kind = models.CharField(verbose_name='aina', max_length=10, choices=Kind.choices, default=Kind.RSS)
    url = models.URLField(verbose_name='anwani', max_length=1000, help_text='Anwani ya mlisho, ukurasa wa orodha au API ya GDELT.')
    language = models.CharField(verbose_name='lugha', max_length=8, blank=True, help_text='en au sw; ukiacha wazi, lugha hutambuliwa kwa kila habari.')
    enabled = models.BooleanField(verbose_name='imewashwa', default=True)
    tanzania_focused = models.BooleanField(
        verbose_name='inahusu Tanzania',
        default=True,
        help_text='Zima kwa vyanzo vinavyoripoti nchi nyingine: habari zake zitalazimika kutaja Tanzania.',
    )
    include_pattern = models.CharField(
        verbose_name='mchoro wa viungo vya habari',
        max_length=300, blank=True,
        help_text='Kwa vyanzo vya HTML tu: regex ambayo kiungo lazima kilingane nayo ili kihesabiwe kuwa habari.',
    )
    refresh_pattern = models.CharField(
        verbose_name='mchoro wa kurasa zinazobadilika',
        max_length=300, blank=True,
        help_text='Regex ya kurasa zinazobadilika, kama matangazo ya mubashara. Habari zinazolingana husomwa upya kila utafutaji zikiwa bado kwenye mlisho.',
    )
    is_forum = models.BooleanField(
        verbose_name='ni jukwaa',
        default=False,
        help_text='Machapisho ya wanachama: chapisho la kwanza tu husomwa, na majina na mawasiliano huondolewa.',
    )
    body_xpath = models.CharField(
        verbose_name='XPath ya maandishi',
        max_length=300, blank=True,
        help_text='XPath ya maandishi ya habari, kwa tovuti ambazo usomaji wa kawaida hupata maandishi machache.',
    )
    exclude_patterns = models.TextField(
        verbose_name='viungo vya kurukwa',
        blank=True, help_text='Regex moja kwa kila mstari. Viungo vinavyolingana hurukwa, mfano /sports/.',
    )
    max_articles = models.PositiveIntegerField(verbose_name='habari za juu kwa utafutaji', default=40, help_text='Habari mpya za kusoma kila utafutaji.')
    min_delay_seconds = models.FloatField(
        verbose_name='muda wa kusubiri (sekunde)',
        default=3.0, help_text='Muda wa chini wa kusubiri kati ya maombi kwa tovuti moja. Muda mrefu zaidi wa robots.txt hufuatwa.',
    )
    last_crawled_at = models.DateTimeField(verbose_name='ilisomwa mwisho', null=True, blank=True)
    last_error = models.TextField(verbose_name='hitilafu ya mwisho', blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'chanzo'
        verbose_name_plural = 'vyanzo'

    def __str__(self):
        return self.name


class Sector(models.Model):
    slug = models.SlugField(verbose_name='kitambulisho', unique=True)
    name = models.CharField(verbose_name='jina (Kiingereza)', max_length=80)
    name_sw = models.CharField('jina (Kiswahili)', max_length=80, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'sekta'
        verbose_name_plural = 'sekta'

    def __str__(self):
        return self.name


class ConcernType(models.TextChoices):
    COMPLAINT = 'complaint', 'Lalamiko'
    GRIEVANCE = 'grievance', 'Manung\'uniko'
    CHALLENGE = 'challenge', 'Changamoto'
    CONCERN = 'concern', 'Wasiwasi'


class Keyword(models.Model):
    """A classifier term. `*` matches any letters, so *lalamik* finds wanalalamika."""

    class Kind(models.TextChoices):
        SECTOR = 'sector', 'Neno la sekta'
        CONCERN = 'concern', 'Neno la kero'
        VOICE = 'voice', 'Neno la walioathirika'

    term = models.CharField(verbose_name='neno', max_length=80)
    kind = models.CharField(verbose_name='aina', max_length=10, choices=Kind.choices)
    sector = models.ForeignKey(Sector, verbose_name='sekta', null=True, blank=True, on_delete=models.CASCADE, related_name='keywords')
    concern_type = models.CharField(verbose_name='aina ya kero', max_length=12, choices=ConcernType.choices, blank=True)
    weight = models.PositiveSmallIntegerField(verbose_name='uzito', default=2)
    active = models.BooleanField(verbose_name='linatumika', default=True)

    class Meta:
        ordering = ['kind', 'term']
        verbose_name = 'neno'
        verbose_name_plural = 'maneno'
        constraints = [
            models.UniqueConstraint(fields=['term', 'kind', 'sector', 'concern_type'], name='unique_keyword'),
            models.CheckConstraint(
                condition=(
                    Q(kind='sector', sector__isnull=False, concern_type='')
                    | Q(kind='concern', sector__isnull=True) & ~Q(concern_type='')
                    | Q(kind='voice', sector__isnull=True, concern_type='')
                ),
                name='keyword_fields_match_kind',
            ),
        ]

    def __str__(self):
        return self.term


class ArticleQuerySet(models.QuerySet):
    def concerns(self):
        """What the dashboard shows: flagged and not rejected, or confirmed by a reviewer."""
        return self.filter(
            Q(is_concern=True) & ~Q(review_status=Article.Review.REJECTED)
            | Q(review_status=Article.Review.CONFIRMED)
        )


class Article(models.Model):
    class Review(models.TextChoices):
        UNREVIEWED = 'unreviewed', 'Haijakaguliwa'
        CONFIRMED = 'confirmed', 'Imethibitishwa'
        REJECTED = 'rejected', 'Imekataliwa'

    source = models.ForeignKey(Source, verbose_name='chanzo', on_delete=models.PROTECT, related_name='articles')
    url = models.URLField(verbose_name='anwani', max_length=1000, unique=True)
    content_hash = models.CharField(verbose_name='alama ya maandishi', max_length=64, db_index=True)
    title = models.CharField(verbose_name='kichwa', max_length=500)
    body = models.TextField(verbose_name='maandishi', blank=True)
    summary = models.TextField(verbose_name='muhtasari', blank=True)
    author = models.CharField(verbose_name='mwandishi', max_length=200, blank=True)
    language = models.CharField(verbose_name='lugha', max_length=8, blank=True, db_index=True)
    body_is_partial = models.BooleanField(
        verbose_name='muhtasari tu',
        default=False, help_text='Muhtasari wa mlisho tu ulitumika, hivyo uchambuzi uliona maandishi machache.',
    )
    is_paid_content = models.BooleanField(
        verbose_name='habari ya kulipia',
        default=False, help_text='Mchapishaji ameweka habari hii kuwa ya kulipia, hivyo maandishi kamili hayakuhifadhiwa.',
    )
    published_at = models.DateTimeField(verbose_name='ilichapishwa', db_index=True)
    published_is_estimated = models.BooleanField(verbose_name='tarehe imekadiriwa', default=False)
    fetched_at = models.DateTimeField(verbose_name='ilisomwa', default=timezone.now)

    is_concern = models.BooleanField(verbose_name='ni kero', default=False, db_index=True)
    concern_score = models.PositiveIntegerField(verbose_name='alama', default=0)
    concern_type = models.CharField(verbose_name='aina ya kero', max_length=12, choices=ConcernType.choices, blank=True, db_index=True)
    region = models.CharField(verbose_name='mkoa', max_length=40, blank=True, db_index=True)
    sectors = models.ManyToManyField(Sector, verbose_name='sekta', through='ArticleSector', related_name='articles')
    matched_terms = models.JSONField(verbose_name='maneno yaliyolingana', default=dict, blank=True)

    review_status = models.CharField(verbose_name='ukaguzi', max_length=12, choices=Review.choices, default=Review.UNREVIEWED, db_index=True)

    objects = ArticleQuerySet.as_manager()

    class Meta:
        ordering = ['-published_at', '-id']
        verbose_name = 'habari'
        verbose_name_plural = 'habari'

    def __str__(self):
        return self.title

    @property
    def paragraphs(self):
        # The extractor marks list items with "- ", which is not part of the publisher's text.
        lines = (line.strip().removeprefix('- ') for line in self.body.splitlines())
        return [line for line in lines if line]


class ArticleSector(models.Model):
    article = models.ForeignKey(Article, verbose_name='habari', on_delete=models.CASCADE)
    sector = models.ForeignKey(Sector, verbose_name='sekta', on_delete=models.CASCADE)
    score = models.PositiveIntegerField(verbose_name='alama', default=0)

    class Meta:
        ordering = ['-score']
        verbose_name = 'sekta ya habari'
        verbose_name_plural = 'sekta za habari'
        constraints = [models.UniqueConstraint(fields=['article', 'sector'], name='unique_article_sector')]


class SkippedUrl(models.Model):
    """A link fetched once and rejected, remembered so later crawls do not fetch it again."""

    url = models.URLField(max_length=1000, unique=True)
    skipped_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.url


class CrawlRun(models.Model):
    source = models.ForeignKey(Source, verbose_name='chanzo', on_delete=models.CASCADE, related_name='runs')
    started_at = models.DateTimeField(verbose_name='ilianza', default=timezone.now)
    finished_at = models.DateTimeField(verbose_name='ilimalizika', null=True, blank=True)
    discovered = models.PositiveIntegerField(verbose_name='zilizopatikana', default=0)
    saved = models.PositiveIntegerField(verbose_name='zilizohifadhiwa', default=0)
    refreshed = models.PositiveIntegerField(verbose_name='zilizosasishwa', default=0, help_text='Kurasa zinazobadilika ambazo maandishi yake yalisasishwa.')
    flagged = models.PositiveIntegerField(verbose_name='kero', default=0)
    duplicates = models.PositiveIntegerField(verbose_name='nakala', default=0)
    skipped = models.PositiveIntegerField(verbose_name='zilizorukwa', default=0, help_text='Zilizotengwa, za zamani mno, au zisizohusu Tanzania.')
    blocked_by_robots = models.PositiveIntegerField(verbose_name='zilizozuiwa na robots.txt', default=0)
    errors = models.PositiveIntegerField(verbose_name='hitilafu', default=0)
    error_detail = models.TextField(verbose_name='maelezo ya hitilafu', blank=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'kumbukumbu ya utafutaji'
        verbose_name_plural = 'kumbukumbu za utafutaji'

    def __str__(self):
        return f'{self.source} at {self.started_at:%Y-%m-%d %H:%M}'
