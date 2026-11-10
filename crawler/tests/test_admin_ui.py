import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase
from django.urls import reverse


class SignInTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('mhariri', 'mhariri@example.com', 'siri-ndefu-sana-123')

    def test_sign_in_page_is_in_swahili_with_reset_link(self):
        page = self.client.get(reverse('admin:login')).content.decode()
        self.assertIn('<h1 class="auth-heading">Ingia</h1>', page)
        self.assertIn('Jina la mtumiaji', page)
        self.assertIn('Nikumbuke', page)
        self.assertIn(reverse('admin_password_reset'), page)
        self.assertIn('data-reveal="id_password"', page)

    def sign_in(self, remember):
        data = {'username': 'mhariri', 'password': 'siri-ndefu-sana-123', 'next': '/admin/'}
        if remember:
            data['remember'] = '1'
        response = self.client.post(reverse('admin:login'), data)
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)

    def test_without_remember_me_the_session_ends_with_the_browser(self):
        self.sign_in(remember=False)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_remember_me_keeps_the_session(self):
        self.sign_in(remember=True)
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_wrong_password_shows_an_error(self):
        response = self.client.post(reverse('admin:login'), {'username': 'mhariri', 'password': 'kosa'})
        self.assertContains(response, 'alert-error')

    def test_password_reset_flow(self):
        response = self.client.post(reverse('admin_password_reset'), {'email': 'mhariri@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('Weka nenosiri jipya', message.subject)
        self.assertIn('Jina lako la mtumiaji ni: mhariri', message.body)

        link = re.search(r'https?://[^/]+(/reset/\S+)', message.body).group(1)
        form_page = self.client.get(link, follow=True)
        self.assertContains(form_page, 'Weka nenosiri jipya')
        response = self.client.post(form_page.redirect_chain[-1][0],
                                    {'new_password1': 'mpya-kabisa-456!', 'new_password2': 'mpya-kabisa-456!'})
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.assertTrue(self.client.login(username='mhariri', password='mpya-kabisa-456!'))

    def test_unknown_email_reveals_nothing(self):
        response = self.client.post(reverse('admin_password_reset'), {'email': 'hakuna@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_signed_out_page(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('admin:logout'))
        self.assertContains(response, 'Umetoka')
        self.assertContains(response, 'Ingia tena')

    def test_change_password_page_uses_the_shell(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('admin:password_change')).content.decode()
        self.assertIn('Nenosiri la sasa', page)
        self.assertIn('class="side-nav"', page)


class ShellTests(TestCase):
    def test_sidebar_lists_only_what_the_user_may_open(self):
        editor = get_user_model().objects.create_user('mkaguzi', password='x', is_staff=True)
        editor.user_permissions.add(Permission.objects.get(codename='view_article'))
        self.client.force_login(editor)
        page = self.client.get(reverse('admin:crawler_article_changelist')).content.decode()
        self.assertIn('href="/admin/crawler/article/" aria-current="page"', page)
        self.assertNotIn('/admin/crawler/source/', page)
        self.assertNotIn('/admin/auth/user/', page)
        self.assertIn('Mhariri', page)


class SiteSignInTests(TestCase):
    """The news pages are only for signed-in users; any active account may read them."""

    @classmethod
    def setUpTestData(cls):
        cls.reader = get_user_model().objects.create_user('msomaji', password='siri-ndefu-sana-123')

    def test_every_news_page_sends_visitors_to_sign_in(self):
        for url in (reverse('dashboard'), reverse('article', args=[1]), reverse('export_csv')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)

    def test_sign_in_and_password_reset_stay_open(self):
        for url in (reverse('login'), reverse('admin:login'), reverse('admin_password_reset')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_reader_signs_in_and_returns_to_the_page_they_wanted(self):
        response = self.client.post(f"{reverse('login')}?next=/export.csv", {
            'username': 'msomaji', 'password': 'siri-ndefu-sana-123', 'next': '/export.csv'})
        self.assertRedirects(response, '/export.csv', fetch_redirect_response=False)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_wrong_password_is_refused(self):
        response = self.client.post(reverse('login'), {'username': 'msomaji', 'password': 'kosa'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)

    def test_reader_gets_the_shell_without_admin_menus(self):
        self.client.force_login(self.reader)
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('class="side-nav"', page)
        self.assertIn('Kero za wananchi', page)
        self.assertIn('Msomaji', page)
        self.assertIn(f'action="{reverse("logout")}"', page)
        self.assertNotIn(f'href="{reverse("admin:index")}"', page)
        self.assertNotIn(reverse('admin:password_change'), page)

    def test_staff_get_the_admin_menus_on_the_dashboard(self):
        self.client.force_login(get_user_model().objects.create_superuser('mhariri', 'm@example.com', 'siri-ndefu-sana-123'))
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('class="side-nav"', page)
        self.assertIn(reverse('admin:crawler_source_changelist'), page)
        self.assertIn('Muhtasari wa usimamizi', page)

    def test_sign_out_returns_to_sign_in(self):
        self.client.force_login(self.reader)
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)

    def test_signed_in_reader_skips_the_sign_in_page(self):
        self.client.force_login(self.reader)
        self.assertRedirects(self.client.get(reverse('login')), reverse('dashboard'))


class ReaderAccountTests(TestCase):
    def test_reader_can_change_their_password(self):
        reader = get_user_model().objects.create_user('msomaji', password='siri-ndefu-sana-123')
        self.client.force_login(reader)
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn(reverse('password_change'), page)
        response = self.client.post(reverse('password_change'), {
            'old_password': 'siri-ndefu-sana-123',
            'new_password1': 'jipya-ndefu-sana-456', 'new_password2': 'jipya-ndefu-sana-456'})
        self.assertRedirects(response, reverse('password_change_done'))
        reader.refresh_from_db()
        self.assertTrue(reader.check_password('jipya-ndefu-sana-456'))

    def test_sign_in_page_shows_no_figures(self):
        self.assertNotIn('Kero 0 kutoka', self.client.get(reverse('login')).content.decode())

    def test_reset_page_warns_when_email_is_not_set_up(self):
        self.assertContains(self.client.get(reverse('admin_password_reset')), 'haujawekewa barua pepe')


class CreateUserTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('mkuu', 'mkuu@example.com', 'siri-ndefu-sana-123')

    def setUp(self):
        self.client.force_login(self.admin)

    def add(self, **fields):
        data = {'username': 'mpya', 'first_name': 'Asha', 'last_name': 'Juma', 'email': 'asha@example.com',
                'role': 'mhariri', 'password1': 'nenosiri-refu-123', 'password2': 'nenosiri-refu-123', **fields}
        return self.client.post(reverse('admin:auth_user_add'), data)

    def test_form_is_one_page_in_swahili_with_two_roles(self):
        page = self.client.get(reverse('admin:auth_user_add')).content.decode()
        self.assertIn('Mhariri: huduma zote', page)
        self.assertIn('Msimamizi mkuu: kila kitu', page)
        self.assertNotIn('value="msomaji"', page)
        self.assertNotIn('Password-based authentication', page)
        self.assertNotIn('you’ll be able to edit more user options', page)

    def test_staff_is_created_ready_to_sign_in(self):
        response = self.add()
        self.assertRedirects(response, reverse('admin:auth_user_changelist'))
        user = get_user_model().objects.get(username='mpya')
        self.assertEqual((user.get_full_name(), user.is_staff, user.is_superuser), ('Asha Juma', True, False))
        self.assertTrue(self.client.login(username='mpya', password='nenosiri-refu-123'))

    def test_staff_get_every_service_but_user_management(self):
        self.add()
        user = get_user_model().objects.get(username='mpya')
        crawler_perms = Permission.objects.filter(content_type__app_label='crawler')
        self.assertTrue(crawler_perms.exists())
        for perm in crawler_perms:
            self.assertTrue(user.has_perm(f'crawler.{perm.codename}'), perm.codename)
        for codename in ('add_user', 'change_user', 'view_user', 'delete_user', 'change_group'):
            self.assertFalse(user.has_perm(f'auth.{codename}'), codename)

    def test_superuser_role(self):
        self.add(role='msimamizi')
        self.assertTrue(get_user_model().objects.get(username='mpya').is_superuser)

    def test_reader_role_cannot_be_chosen(self):
        self.add(role='msomaji')
        self.assertFalse(get_user_model().objects.filter(username='mpya').exists())

    def test_email_is_required(self):
        self.add(email='')
        self.assertFalse(get_user_model().objects.filter(username='mpya').exists())

    def test_mismatched_passwords_are_refused(self):
        self.add(password2='tofauti-kabisa-456')
        self.assertFalse(get_user_model().objects.filter(username='mpya').exists())

    def test_staff_cannot_manage_users(self):
        self.add()
        self.client.force_login(get_user_model().objects.get(username='mpya'))
        self.assertEqual(self.client.get(reverse('admin:auth_user_add')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:crawler_source_add')).status_code, 200)
