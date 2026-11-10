from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm
from django.contrib.auth.models import Group, Permission, User
from django.urls import reverse
from django.utils.html import format_html

from .services.crawl_control import STOPPED_NOTE
from .models import Article, ArticleSector, ConcernType, CrawlRun, Keyword, Sector, Source



def badge(tone, icon, label, title=''):
    """A status label. The icon and text carry the meaning, so it never relies on colour alone."""
    return format_html('<span class="badge badge-{}" title="{}"><span aria-hidden="true">{}</span> {}</span>',
                       tone, title, icon, label)


class SourceStatusFilter(admin.SimpleListFilter):
    title = 'hali'
    parameter_name = 'hali'

    def lookups(self, request, model_admin):
        return [('sawa', 'Inafanya kazi'), ('hitilafu', 'Hitilafu'), ('imezimwa', 'Imezimwa')]

    def queryset(self, request, queryset):
        if self.value() == 'sawa':
            return queryset.filter(enabled=True, last_error='')
        if self.value() == 'hitilafu':
            return queryset.filter(enabled=True).exclude(last_error='')
        if self.value() == 'imezimwa':
            return queryset.filter(enabled=False)
        return queryset


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'status', 'kind', 'language', 'is_forum', 'enabled', 'last_crawled_at']
    list_filter = [SourceStatusFilter, 'kind', 'enabled', 'is_forum']
    list_editable = ['enabled']
    search_fields = ['name', 'url']

    @admin.display(description='Hali')
    def status(self, source):
        if not source.enabled:
            return badge('neutral', '–', 'Imezimwa')
        if source.last_error:
            return badge('critical', '✕', 'Hitilafu', source.last_error)
        if not source.last_crawled_at:
            return badge('neutral', '…', 'Bado haijasomwa')
        return badge('good', '✓', 'Inafanya kazi')


@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ['name', 'name_sw', 'slug']
    prepopulated_fields = {'slug': ['name']}


@admin.register(Keyword)
class KeywordAdmin(admin.ModelAdmin):
    list_display = ['term', 'kind', 'sector', 'concern_type', 'weight', 'active']
    list_filter = ['kind', 'active', 'sector', 'concern_type']
    list_editable = ['weight', 'active']
    search_fields = ['term']


class ArticleSectorInline(admin.TabularInline):
    model = ArticleSector
    extra = 0


def concern_kind(article):
    if not article.concern_type:
        return ''
    return format_html('<span class="type-swatch type-{}"></span>{}',
                       article.concern_type, ConcernType(article.concern_type).label)


REVIEW_BADGES = {
    Article.Review.UNREVIEWED: ('neutral', '…', 'Inasubiri'),
    Article.Review.CONFIRMED: ('good', '✓', 'Imethibitishwa'),
    Article.Review.REJECTED: ('critical', '✕', 'Imekataliwa'),
}


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ['title', 'source', 'published_at', 'is_concern', 'kind', 'region', 'review', 'review_page']
    list_filter = ['is_concern', 'review_status', 'concern_type', 'language', 'source', 'sectors']
    search_fields = ['title', 'body', 'url']
    date_hierarchy = 'published_at'
    inlines = [ArticleSectorInline]
    actions = ['mark_confirmed', 'mark_rejected']

    @admin.display(description='Aina', ordering='concern_type')
    def kind(self, article):
        return concern_kind(article)

    @admin.display(description='Ukaguzi', ordering='review_status')
    def review(self, article):
        return badge(*REVIEW_BADGES.get(article.review_status, REVIEW_BADGES[Article.Review.UNREVIEWED]))

    @admin.display(description='Kagua')
    def review_page(self, article):
        # The reader page shows why a story was flagged, beside the review buttons.
        return format_html('<a href="{}">Kagua</a>', reverse('article', args=[article.pk]))

    @admin.action(description='Thibitisha kuwa ni kero za wananchi')
    def mark_confirmed(self, request, queryset):
        count = queryset.update(review_status=Article.Review.CONFIRMED)
        self.message_user(request, f'Habari {count} zimethibitishwa kuwa kero za wananchi.', messages.SUCCESS)

    @admin.action(description='Kataa: si kero za wananchi')
    def mark_rejected(self, request, queryset):
        count = queryset.update(review_status=Article.Review.REJECTED)
        self.message_user(request, f'Habari {count} zimekataliwa na hazitaonekana kwenye orodha.', messages.SUCCESS)


@admin.register(CrawlRun)
class CrawlRunAdmin(admin.ModelAdmin):
    list_display = ['source', 'started_at', 'duration', 'outcome', 'discovered', 'saved', 'flagged']
    list_filter = ['source']
    readonly_fields = [field.name for field in CrawlRun._meta.fields]

    @admin.display(description='Muda')
    def duration(self, run):
        if not run.finished_at:
            return 'inaendelea'
        seconds = int((run.finished_at - run.started_at).total_seconds())
        return f'dak {seconds // 60}, sek {seconds % 60}' if seconds >= 60 else f'sek {seconds}'

    @admin.display(description='Matokeo', ordering='errors')
    def outcome(self, run):
        return run_outcome(run)


def run_outcome(run):
    if not run.finished_at:
        return badge('warning', '…', 'Inaendelea')
    if run.error_detail == STOPPED_NOTE:
        return badge('neutral', '■', 'Ilisimamishwa')
    if run.errors:
        last = run.error_detail.splitlines()[-1] if run.error_detail else ''
        return badge('critical', '✕', f'Hitilafu {run.errors}', last)
    return badge('good', '✓', 'Sawa')


# ---------- Users ----------

EDITORS_GROUP = 'Wahariri'


def editors_group():
    """The staff group: every permission in the crawler app, and none over user accounts.

    Permissions are added each time, so models added later are included; any extra ones given
    under Makundi are kept.
    """
    group, _ = Group.objects.get_or_create(name=EDITORS_GROUP)
    group.permissions.add(*Permission.objects.filter(content_type__app_label='crawler'))
    return group


class Role:
    READER, EDITOR, SUPERUSER = 'msomaji', 'mhariri', 'msimamizi'
    # Readers can still exist (created before, or by unticking "staff" on a user), but the form
    # only creates the two roles that use the admin.
    CHOICES = [
        (EDITOR, 'Mhariri: huduma zote (kutafuta habari, kukagua, vyanzo, maneno, sekta), isipokuwa watumiaji'),
        (SUPERUSER, 'Msimamizi mkuu: kila kitu, pamoja na kuongeza na kusimamia watumiaji'),
    ]
    LABELS = {READER: 'Msomaji', EDITOR: 'Mhariri', SUPERUSER: 'Msimamizi mkuu'}

    @classmethod
    def of(cls, user):
        return cls.SUPERUSER if user.is_superuser else cls.EDITOR if user.is_staff else cls.READER


class NewUserForm(BaseUserCreationForm):
    """Everything about a new account on one page, so it is ready to use as soon as it is saved."""
    role = forms.ChoiceField(label='Nafasi', choices=Role.CHOICES, initial=Role.EDITOR, widget=forms.RadioSelect)

    class Meta(BaseUserCreationForm.Meta):
        fields = ('username', 'first_name', 'last_name', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].help_text = 'Atakalotumia kuingia. Herufi, namba na @ . + - _ tu, bila nafasi.'
        self.fields['email'].required = True
        self.fields['email'].help_text = 'Inahitajika ili mtumiaji aweze kurejesha nenosiri lililosahaulika.'
        self.fields['password1'].help_text = (
            'Angalau herufi 8. Lisiwe nenosiri linalotumika sana, lisiwe namba tu, '
            'wala lisifanane na jina la mtumiaji.')
        self.fields['password2'].help_text = 'Andika nenosiri lile lile tena.'

    def save(self, commit=True):
        user = super().save(commit=False)
        role = self.cleaned_data['role']
        user.is_staff = True
        user.is_superuser = role == Role.SUPERUSER
        if commit:
            user.save()
            self.save_m2m()
        return user


admin.site.unregister(User)


@admin.register(User)
class SautiUserAdmin(UserAdmin):
    add_form = NewUserForm
    add_form_template = 'admin/change_form.html'  # Without Django's note about a second step.
    add_fieldsets = (
        ('Akaunti', {'fields': ('username', 'first_name', 'last_name', 'email')}),
        ('Nafasi', {'fields': ('role',)}),
        ('Nenosiri', {'fields': ('password1', 'password2')}),
    )
    list_display = ('username', 'full_name', 'email', 'role', 'is_active', 'last_login')
    list_filter = ('is_staff', 'is_superuser', 'is_active')

    @admin.display(description='jina')
    def full_name(self, user):
        return user.get_full_name()

    @admin.display(description='nafasi')
    def role(self, user):
        return Role.LABELS[Role.of(user)]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change and Role.of(obj) == Role.EDITOR:
            obj.groups.add(editors_group())

    def response_add(self, request, obj, post_url_continue=None):
        # The account is complete once saved, so go back to the list instead of Django's second step.
        return admin.ModelAdmin.response_add(self, request, obj, post_url_continue)
