"""The admin site, with the project's own sidebar and a "remember me" choice at sign-in."""
from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig
from django.contrib.auth.decorators import login_not_required
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache


def apply_remember_choice(request):
    """Without "remember me" ticked, the session ends when the browser closes."""
    if request.method == 'POST' and request.user.is_authenticated and not request.POST.get('remember'):
        request.session.set_expiry(0)


class SautiAdminSite(AdminSite):
    site_header = 'Sauti ya Wananchi'
    site_title = 'Sauti ya Wananchi'
    index_title = 'Muhtasari'
    enable_nav_sidebar = False  # The project's base template draws its own sidebar.

    @method_decorator(never_cache)
    @login_not_required  # Overriding login drops the parent's markers, so they are repeated here.
    def login(self, request, extra_context=None):
        response = super().login(request, extra_context)
        apply_remember_choice(request)
        return response


class SautiAdminConfig(AdminConfig):
    default_site = 'crawler.admin_site.SautiAdminSite'
