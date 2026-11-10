from datetime import timedelta

from django import template
from django.utils import timezone

from crawler.models import Article, CrawlRun, Source
from crawler.services import crawl_control

register = template.Library()

MODEL_ICONS = {
    'Article': 'newspaper',
    'CrawlRun': 'clock',
    'Keyword': 'tag',
    'Sector': 'squares-2x2',
    'Source': 'rss',
    'Group': 'user-group',
    'User': 'users',
}


@register.filter
def model_icon(object_name):
    return MODEL_ICONS.get(object_name, 'inbox')


@register.filter
def initials(user):
    """Up to two letters for the avatar: from the full name if there is one, else the username."""
    name = (user.get_full_name() or user.get_username() or '?').strip()
    parts = name.split()
    letters = parts[0][0] + parts[-1][0] if len(parts) > 1 else name[:2]
    return letters.upper()


@register.simple_tag(takes_context=True)
def nav_apps(context):
    """The admin's apps for the sidebar: already in the context on admin pages, worked out on the others."""
    if 'available_apps' in context:
        return context['available_apps']
    request = context.get('request')
    if request is not None and request.user.is_staff:
        from django.contrib import admin
        return admin.site.get_app_list(request)
    return []


@register.simple_tag
def crawl_is_running():
    state = crawl_control.read_state()
    if state and not state.get('finished_at'):
        return crawl_control._process_alive(state['pid'])
    return False


@register.simple_tag
def pending_review_count():
    return Article.objects.concerns().filter(review_status=Article.Review.UNREVIEWED).count()


@register.simple_tag(takes_context=True)
def crawl_overview(context):
    """The figures, lists and crawl status shown on the admin home page."""
    from crawler.views import _crawl_panel_context, _visible_concerns

    request = context['request']
    panel = _crawl_panel_context(request)
    week_ago = timezone.now() - timedelta(days=7)
    pending = Article.objects.concerns().filter(review_status=Article.Review.UNREVIEWED)
    enabled = Source.objects.filter(enabled=True)
    return {
        'pending_review': pending.count(),
        'pending_list': pending.select_related('source')[:6],
        'new_this_week': _visible_concerns().filter(published_at__gte=week_ago).count(),
        'new_last_week': _visible_concerns().filter(
            published_at__gte=week_ago - timedelta(days=7), published_at__lt=week_ago).count(),
        'sources_enabled': enabled.count(),
        # A source counts as working only once it has been read without an error.
        'sources_working': enabled.filter(last_error='').exclude(last_crawled_at=None).count(),
        'failing_sources': enabled.exclude(last_error='').count(),
        'unread_sources': enabled.filter(last_error='', last_crawled_at=None).count(),
        'articles_stored': Article.objects.count(),
        'recent_runs': CrawlRun.objects.select_related('source')[:6],
        'crawl': panel.get('crawl'),
        'crawl_error': panel.get('crawl_error', ''),
    }


@register.filter
def run_outcome(run):
    from crawler.admin import run_outcome as outcome
    return outcome(run)


@register.filter
def concern_kind(article):
    from crawler.admin import concern_kind as kind
    return kind(article)
