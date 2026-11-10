import csv
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import views as auth_views
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .admin_site import apply_remember_choice
from .models import Article, ConcernType, Sector, Source
from .services import crawl_control

PERIODS = {'7': 'Siku 7 zilizopita', '30': 'Siku 30 zilizopita', '90': 'Siku 90 zilizopita', 'all': 'Muda wote'}
DEFAULT_PERIOD = '30'
# Newest first unless the reader asks for the strongest matches first; '' keeps the default out of URLs.
ORDERS = {'': 'Mpya kwanza', 'score': 'Alama za juu kwanza'}
NO_SECTOR = 'none'
PAGE_SIZE = 25
CSV_MAX_ROWS = 10000
CSV_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _selected_filters(request):
    get = request.GET.get
    period = get('period', DEFAULT_PERIOD)
    return {
        'q': get('q', '').strip(),
        'sector': get('sector', ''),
        'type': get('type', ''),
        'region': get('region', ''),
        'source': get('source', ''),
        'period': period if period in PERIODS else DEFAULT_PERIOD,
        'order': get('order', '') if get('order', '') in ORDERS else '',
    }


def _visible_concerns():
    # A story with no text to show (a paid article with no public summary) is left out.
    return Article.objects.concerns().filter(language__in=settings.CRAWLER['LANGUAGES']).exclude(body='')


def _filtered(filters, *, ignore=()):
    """Apply the selected filters, leaving out `ignore`, so a chart can show its own alternatives."""
    articles = _visible_concerns()
    active = {key: value for key, value in filters.items() if key not in ignore}
    if active.get('period') in ('7', '30', '90'):
        articles = articles.filter(published_at__gte=timezone.now() - timedelta(days=int(active['period'])))
    if active.get('q'):
        articles = articles.filter(Q(title__icontains=active['q']) | Q(body__icontains=active['q']))
    if active.get('sector') == NO_SECTOR:
        articles = articles.filter(sectors__isnull=True)
    elif active.get('sector'):
        articles = articles.filter(sectors__slug=active['sector'])
    if active.get('type'):
        articles = articles.filter(concern_type=active['type'])
    if active.get('region'):
        articles = articles.filter(region=active['region'])
    if active.get('source', '').isdigit():
        articles = articles.filter(source_id=int(active['source']))
    if active.get('order') == 'score':
        articles = articles.order_by('-concern_score', '-published_at', '-id')
    return articles


def _option_counts(filters, key, field):
    """Counts for one dropdown, keeping every other filter, so its choices match what the list can show."""
    return dict(
        _filtered(filters, ignore=(key,)).exclude(**{field: None}).values_list(field)
        .order_by().annotate(total=Count('id', distinct=True))
    )


def _sector_bars(filters):
    """Every sector with its count, keeping the selected filters except the sector itself."""
    articles = _filtered(filters, ignore=('sector',))
    counts = dict(
        articles.values_list('sectors__slug').order_by().annotate(total=Count('id', distinct=True))
    )
    unsorted_count = counts.pop(None, 0)
    rows = [
        {'slug': sector.slug, 'name': sector.name_sw or sector.name, 'total': counts.get(sector.slug, 0)}
        for sector in Sector.objects.all()
    ]
    rows.sort(key=lambda row: (-row['total'], row['name']))
    if unsorted_count:
        rows.append({'slug': NO_SECTOR, 'name': 'Haijapangiwa sekta', 'total': unsorted_count})

    top = max((row['total'] for row in rows), default=0)
    base = {key: value for key, value in filters.items() if value and key != 'sector'}
    for row in rows:
        row['percent'] = round(row['total'] / top * 100) if top else 0
        row['selected'] = filters['sector'] == row['slug']
        target = base if row['selected'] else {**base, 'sector': row['slug']}
        row['query'] = urlencode(target)
    return rows


FILTER_KEYS = ('q', 'sector', 'type', 'region', 'source')

PERIOD_OPENINGS = {
    '7': 'Siku 7 zilizopita', '30': 'Siku 30 zilizopita', '90': 'Siku 90 zilizopita', 'all': 'Tangu ukusanyaji uanze',
}


def _summary(filters, sector_bars, total):
    """The page's headline, written from the data: what most concerns were about in the period.

    Returns the parts of one sentence, so the template can turn the sector names into filter links.
    """
    opening = PERIOD_OPENINGS[filters['period']]
    if not total:
        if any(filters[key] for key in FILTER_KEYS):
            text = 'hakuna kero zilizopatikana kwa vichujio hivi.'
        elif filters['period'] != 'all':
            text = 'hakuna kero mpya zilizopatikana.'
        else:
            text = 'hakuna kero zilizopatikana bado.'
        return {'opening': opening, 'text': text}
    selected = next((bar for bar in sector_bars if bar['selected']), None)
    if selected:
        # Already named in the filter chips, where it can be removed; here it is plain text.
        return {'opening': opening, 'text': f'kero {total} zilihusu', 'selected_sector': selected['name'], 'end': '.'}
    top = [bar for bar in sector_bars if bar['total'] and bar['slug'] != NO_SECTOR][:3]
    if not top:
        return {'opening': opening, 'text': f'kero {total} zilipatikana.'}
    return {'opening': opening, 'text': 'kero nyingi zaidi zilihusu', 'sectors': top, 'end': '.'}


def _type_counts(filters):
    """Each kind of concern with its count; doubles as the key to the colours in the story list."""
    counts = dict(_filtered(filters, ignore=('type',)).values_list('concern_type')
                  .order_by().annotate(total=Count('id', distinct=True)))
    base = {key: value for key, value in filters.items() if value and key != 'type'}
    rows = []
    for value, label in ConcernType.choices:
        selected = filters['type'] == value
        rows.append({
            'value': value, 'label': label, 'total': counts.get(value, 0), 'selected': selected,
            'query': urlencode(base if selected else {**base, 'type': value}),
        })
    return rows


def _active_chips(filters, sources):
    """The filters in use, each with a link that removes just that one."""
    names = {
        'q': lambda v: f'“{v}”',
        'sector': lambda v: 'Haijapangiwa sekta' if v == NO_SECTOR else next(
            (s.name_sw or s.name for s in Sector.objects.filter(slug=v)), v),
        'type': lambda v: dict(ConcernType.choices).get(v, v),
        'region': lambda v: v,
        'source': lambda v: next((s.name for s in sources if str(s.pk) == v), v),
    }
    chips = []
    for key, name in names.items():
        if filters[key]:
            rest = {k: v for k, v in filters.items() if v and k != key}
            chips.append({'label': name(filters[key]), 'query': urlencode(rest)})
    return chips


def _time_ago(moment):
    """Django's Swahili translation leaves time spans in English, so they are written out here."""
    minutes = int((timezone.now() - moment).total_seconds() // 60)
    if minutes < 1:
        return 'sasa hivi'
    if minutes < 60:
        return f'dakika {minutes} zilizopita'
    if minutes < 24 * 60:
        return f'saa {minutes // 60} zilizopita'
    return f'siku {minutes // (24 * 60)} zilizopita'


class SiteLoginView(auth_views.LoginView):
    """Sign-in for anyone with an active account. The admin keeps its own, staff-only sign-in."""
    template_name = 'admin/login.html'
    redirect_authenticated_user = True
    extra_context = {'intro': 'Ingia ili kusoma kero za wananchi zilizokusanywa kutoka habari na majukwaa.'}

    def form_valid(self, form):
        response = super().form_valid(form)
        apply_remember_choice(self.request)
        return response


class SiteLogoutView(auth_views.LogoutView):
    next_page = 'login'  # The admin's own sign-out still shows its "signed out" page.


def dashboard(request):
    last_crawl = (
        Source.objects.exclude(last_crawled_at=None).order_by('-last_crawled_at')
        .values_list('last_crawled_at', flat=True).first()
    )
    filters = _selected_filters(request)
    articles = _filtered(filters).distinct().select_related('source').prefetch_related('sectors')
    page = Paginator(articles, PAGE_SIZE).get_page(request.GET.get('page'))
    active_query = urlencode({key: value for key, value in filters.items() if value})
    sector_bars = _sector_bars(filters)
    sources = Source.objects.filter(articles__in=_visible_concerns()).distinct()
    has_filters = any(filters[key] for key in FILTER_KEYS)
    # With nothing in the chosen period, say how many there are over all time instead of "none yet".
    outside_period = 0
    if not page.paginator.count and filters['period'] != 'all':
        outside_period = _filtered({**filters, 'period': 'all'}).distinct().count()
    region_counts = _option_counts(filters, 'region', 'region')
    source_counts = _option_counts(filters, 'source', 'source')
    regions = [
        {'value': region, 'total': region_counts.get(region, 0)}
        for region in _visible_concerns().exclude(region='').order_by('region').values_list('region', flat=True).distinct()
        if region_counts.get(region) or region == filters['region']
    ]
    source_options = [
        {'pk': source.pk, 'name': source.name, 'total': source_counts.get(source.pk, 0)}
        for source in sources
        if source_counts.get(source.pk) or str(source.pk) == filters['source']
    ]
    # The page the reader is on, so a story's "back" link returns to it.
    back_query = urlencode({**{k: v for k, v in filters.items() if v}, **({'page': page.number} if page.number > 1 else {})})
    return render(request, 'crawler/dashboard.html', {
        'summary': _summary(filters, sector_bars, page.paginator.count),
        'type_counts': _type_counts(filters),
        'chips': _active_chips(filters, sources),
        'source_count': _filtered(filters).values('source').distinct().count(),
        'page': page,
        'filters': filters,
        'active_query': active_query,
        'sector_bars': sector_bars,
        'periods': PERIODS,
        'period_label': PERIODS[filters['period']].lower(),
        'concern_types': ConcernType.choices,
        'sectors': Sector.objects.all(),
        'sources': sources,
        'regions': regions,
        'source_options': source_options,
        'orders': ORDERS,
        'back_query': back_query,
        'outside_period': outside_period,
        'all_time_query': urlencode({**{k: v for k, v in filters.items() if v}, 'period': 'all'}),
        'csv_capped': page.paginator.count > CSV_MAX_ROWS,
        'csv_max_rows': CSV_MAX_ROWS,
        'stored_total': Article.objects.filter(language__in=settings.CRAWLER['LANGUAGES']).count(),
        'last_crawl_ago': _time_ago(last_crawl) if last_crawl else '',
        'has_filters': has_filters,
        **_crawl_panel_context(request),
    })


def _crawl_panel_context(request):
    if not request.user.is_staff:
        return {}
    status = crawl_control.crawl_status()
    if status['started_at']:
        status['started_ago'] = _time_ago(status['started_at'])
    if status.get('finished_at'):
        status['finished_ago'] = _time_ago(status['finished_at'])
    return {'crawl': status, 'crawl_error': request.session.pop('crawl_error', '')}


@staff_member_required
@require_POST
def start_crawl(request):
    try:
        crawl_control.start_crawl()
    except crawl_control.CrawlAlreadyRunning:
        request.session['crawl_error'] = 'Utafutaji mwingine tayari unaendelea. Subiri ukamilike.'
    except crawl_control.CrawlFailedToStart as exc:
        request.session['crawl_error'] = f'Utafutaji haukuweza kuanza: {exc}'
    return _back(request)


@staff_member_required
@require_POST
def stop_crawl(request):
    try:
        if not crawl_control.stop_crawl():
            request.session['crawl_error'] = 'Utafutaji unasimama. Inaweza kuchukua sekunde chache.'
    except crawl_control.CrawlNotRunning:
        request.session['crawl_error'] = 'Hakuna utafutaji unaoendelea.'
    except PermissionError:
        request.session['crawl_error'] = (
            'Utafutaji huu ulianzishwa na mtumiaji mwingine wa kompyuta, hivyo hauwezi kusimamishwa hapa.')
    return _back(request)


def _back(request):
    """Return to the page the button was pressed on, if it is on this site."""
    next_url = request.POST.get('next', '')
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect('dashboard')


@staff_member_required
def crawl_panel(request):
    """The progress panel alone, which the dashboard reloads every few seconds during a crawl."""
    context = _crawl_panel_context(request)
    context['just_finished'] = request.GET.get('was_running') == '1' and not context['crawl']['running']
    context['crawl_next'] = request.GET.get('next', '')
    return render(request, 'crawler/_crawl_panel.html', context)


def _back_query(request):
    """The dashboard filters a story was opened from. Only ever appended to the dashboard's own path."""
    return (request.GET.get('back') or request.POST.get('back') or '').lstrip('?')[:500]


def _next_unreviewed(article):
    """The next concern waiting for review, newest first, after this one."""
    return (Article.objects.concerns().filter(review_status=Article.Review.UNREVIEWED).exclude(pk=article.pk)
            .filter(Q(published_at__lt=article.published_at)
                    | Q(published_at=article.published_at, pk__lt=article.pk))
            .order_by('-published_at', '-id').first()
            or Article.objects.concerns().filter(review_status=Article.Review.UNREVIEWED).exclude(pk=article.pk)
            .order_by('-published_at', '-id').first())


def article_detail(request, pk):
    articles = Article.objects.select_related('source')
    if not request.user.is_staff:
        # Readers see only what the list shows them; rejected and unflagged stories stay with staff.
        articles = articles.filter(pk__in=_visible_concerns().values('pk'))
    article = get_object_or_404(articles, pk=pk)
    sector_terms = article.matched_terms.get('sectors', {})
    sectors = [
        {'name': sector.name_sw or sector.name, 'terms': sector_terms.get(sector.slug, [])}
        for sector in article.sectors.all()
    ]
    back = _back_query(request)
    return render(request, 'crawler/article.html', {
        'article': article, 'sectors': sectors, 'back': back,
        'back_url': reverse('dashboard') + (f'?{back}' if back else ''),
        'next_unreviewed': _next_unreviewed(article) if request.user.is_staff else None,
    })


@staff_member_required
@require_POST
def review_article(request, pk):
    article = get_object_or_404(Article, pk=pk)
    decision = request.POST.get('decision')
    if decision in Article.Review.values:
        article.review_status = decision
        article.save(update_fields=['review_status'])
        messages.success(request, REVIEW_MESSAGES[decision].format(title=article.title))
    back = _back_query(request)
    target = article
    if decision in (Article.Review.CONFIRMED, Article.Review.REJECTED) and request.POST.get('then') == 'next':
        target = _next_unreviewed(article) or article
    url = reverse('article', args=[target.pk])
    return redirect(url + (f'?{urlencode({"back": back})}' if back else ''))


REVIEW_MESSAGES = {
    Article.Review.CONFIRMED: '“{title}” imethibitishwa kuwa kero ya wananchi.',
    Article.Review.REJECTED: '“{title}” imekataliwa na haitaonekana kwenye orodha.',
    Article.Review.UNREVIEWED: 'Ukaguzi wa “{title}” umefutwa; inasubiri ukaguzi tena.',
}


def _csv_safe(value):
    """Scraped text opened in a spreadsheet must not run as a formula."""
    text = str(value)
    return f"'{text}" if text.startswith(CSV_FORMULA_PREFIXES) else text


def export_csv(request):
    articles = (
        _filtered(_selected_filters(request)).distinct()
        .select_related('source').prefetch_related('sectors')[:CSV_MAX_ROWS]
    )
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="kero-za-wananchi.csv"'
    response.write('\ufeff')  # Lets Excel recognise UTF-8, so quotes and accents aren't garbled.
    writer = csv.writer(response)
    writer.writerow(['Tarehe', 'Kichwa', 'Aina', 'Sekta', 'Mkoa', 'Chanzo', 'Alama', 'Maandishi'])
    for article in articles:
        writer.writerow([_csv_safe(value) for value in (
            article.published_at.date().isoformat(),
            article.title,
            article.get_concern_type_display(),
            '; '.join(sector.name_sw or sector.name for sector in article.sectors.all()),
            article.region,
            article.source.name,
            article.concern_score,
            article.body,
        )])
    return response
