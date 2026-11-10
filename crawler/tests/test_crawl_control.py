import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crawler.models import CrawlRun, Source
from crawler.services import crawl_control


class CrawlControlTestCase(TestCase):
    """Keeps the state, lock and log files in a temporary folder, away from any real crawl."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user('mhariri', password='x', is_staff=True)
        cls.first = Source.objects.create(name='Chanzo A', kind='rss', url='https://a.example/feed')
        cls.second = Source.objects.create(name='Chanzo B', kind='rss', url='https://b.example/feed')

    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        for name, value in (('STATE_PATH', folder / 'state.json'), ('LOCK_PATH', folder / 'crawl.lock'),
                            ('LOG_PATH', folder / 'crawl.log')):
            patcher = patch.object(crawl_control, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_state(self, **changes):
        state = {'pid': 4321, 'started_at': (timezone.now() - timezone.timedelta(minutes=5)).isoformat(),
                 'finished_at': None, 'sources': ['Chanzo A', 'Chanzo B'], **changes}
        crawl_control.write_state(state)
        return state


class StartButtonTests(CrawlControlTestCase):
    def test_readers_who_are_not_staff_cannot_start_a_crawl(self):
        self.client.force_login(get_user_model().objects.create_user('msomaji', password='siri-ndefu-sana-123'))
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('Tafuta habari mpya</button>', page)
        self.assertNotIn('crawl-panel', page)
        with patch('subprocess.Popen') as popen:
            response = self.client.post(reverse('start_crawl'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])
        popen.assert_not_called()

    def test_staff_see_the_button(self):
        self.client.force_login(self.staff)
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Tafuta habari mpya</button>', page)

    def test_button_launches_the_crawl_command_in_the_background(self):
        self.client.force_login(self.staff)
        process = MagicMock(pid=999)
        process.poll.return_value = 0
        process.returncode = 0
        with patch('subprocess.Popen', return_value=process) as popen:
            response = self.client.post(reverse('start_crawl'))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], [str(crawl_control.settings.BASE_DIR / 'manage.py'), 'crawl'])
        self.assertTrue(popen.call_args.kwargs['start_new_session'])

    def test_second_crawl_is_refused_while_one_runs(self):
        self.client.force_login(self.staff)
        with patch.object(crawl_control, 'lock_is_held', return_value=True), patch('subprocess.Popen') as popen:
            self.client.post(reverse('start_crawl'))
        popen.assert_not_called()
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Utafutaji mwingine tayari unaendelea', page)

    def test_crawl_that_fails_at_once_is_reported(self):
        self.client.force_login(self.staff)
        process = MagicMock(pid=999, returncode=1)
        process.poll.return_value = 1

        def launch(command, stdout, **kwargs):
            stdout.write('Traceback ...\nCommandError: No sources to crawl.\n')
            stdout.flush()
            return process

        with patch('subprocess.Popen', side_effect=launch):
            self.client.post(reverse('start_crawl'))
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Utafutaji haukuweza kuanza: CommandError: No sources to crawl.', page)

    def test_lock_is_detected(self):
        import fcntl
        self.assertFalse(crawl_control.lock_is_held())
        with open(crawl_control.LOCK_PATH, 'w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.assertTrue(crawl_control.lock_is_held())


class ProgressTests(CrawlControlTestCase):
    def test_progress_shows_current_source_and_totals(self):
        state = self.write_state()
        started = timezone.datetime.fromisoformat(state['started_at'])
        CrawlRun.objects.create(source=self.first, started_at=started, finished_at=timezone.now(), saved=3, flagged=1)
        CrawlRun.objects.create(source=self.second, started_at=timezone.now())
        with patch.object(crawl_control, '_process_alive', return_value=True):
            status = crawl_control.crawl_status()
        self.assertTrue(status['running'])
        self.assertEqual((status['done'], status['total'], status['current']), (1, 2, 'Chanzo B'))
        self.assertEqual((status['saved'], status['flagged']), (3, 1))

        self.client.force_login(self.staff)
        with patch.object(crawl_control, '_process_alive', return_value=True):
            page = self.client.get(reverse('crawl_panel')).content.decode()
        self.assertIn('Inasoma <strong>Chanzo B</strong> (chanzo 2 kati ya 2)', page)
        self.assertIn('data-running="true"', page)

    def test_runs_from_earlier_crawls_are_not_counted(self):
        CrawlRun.objects.create(source=self.first, started_at=timezone.now() - timezone.timedelta(days=1),
                                finished_at=timezone.now(), saved=50)
        self.write_state()
        with patch.object(crawl_control, '_process_alive', return_value=True):
            self.assertEqual(crawl_control.crawl_status()['saved'], 0)

    def test_finished_crawl_offers_to_show_the_new_stories(self):
        self.write_state(finished_at=timezone.now().isoformat())
        self.client.force_login(self.staff)
        page = self.client.get(reverse('crawl_panel') + '?was_running=1').content.decode()
        self.assertIn('Utafutaji umekamilika', page)
        self.assertIn('Onyesha habari mpya', page)
        self.assertIn('data-running="false"', page)

    def test_crawl_killed_midway_is_reported_as_stopped(self):
        self.write_state()
        with patch.object(crawl_control, '_process_alive', return_value=False):
            status = crawl_control.crawl_status()
        self.assertFalse(status['running'])
        self.assertTrue(status['stopped_early'])

    def test_status_page_requires_staff(self):
        response = self.client.get(reverse('crawl_panel'))
        self.assertEqual(response.status_code, 302)


class CrawlCommandStateTests(CrawlControlTestCase):
    def test_crawl_command_records_its_progress_file(self):
        def fake_crawl(source, **kwargs):
            return CrawlRun.objects.create(source=source, finished_at=timezone.now())

        with patch('crawler.management.commands.crawl.crawl_source', side_effect=fake_crawl), \
                patch('crawler.management.commands.crawl.settings.BASE_DIR', crawl_control.STATE_PATH.parent):
            call_command('crawl', source=['Chanzo A'], stdout=open(os.devnull, 'w'))
        state = json.loads(crawl_control.STATE_PATH.read_text())
        self.assertEqual(state['sources'], ['Chanzo A'])
        self.assertEqual(state['pid'], os.getpid())
        self.assertIsNotNone(state['finished_at'])


class AdminHomeTests(CrawlControlTestCase):
    def test_admin_home_shows_overview_and_crawl_button(self):
        self.client.force_login(get_user_model().objects.create_superuser('msimamizi', password='x'))
        page = self.client.get(reverse('admin:index')).content.decode()
        self.assertIn('Karibu tena, msimamizi', page)
        self.assertIn('Zinasubiri ukaguzi', page)
        self.assertIn('Tafuta habari mpya</button>', page)
        self.assertIn('name="next" value="/admin/"', page)
        self.assertIn('crawler/admin.css', page)

    def test_button_returns_to_the_admin_when_pressed_there(self):
        self.client.force_login(self.staff)
        process = MagicMock(pid=999, returncode=0)
        process.poll.return_value = 0
        with patch('subprocess.Popen', return_value=process):
            response = self.client.post(reverse('start_crawl'), {'next': '/admin/'})
        self.assertEqual(response['Location'], '/admin/')

    def test_button_does_not_redirect_to_other_sites(self):
        self.client.force_login(self.staff)
        with patch.object(crawl_control, 'lock_is_held', return_value=True):
            response = self.client.post(reverse('start_crawl'), {'next': 'https://evil.example/'})
        self.assertEqual(response['Location'], reverse('dashboard'))

    def test_source_status_filter_and_labels(self):
        Source.objects.filter(pk=self.first.pk).update(last_error='HTTP 429', last_crawled_at=timezone.now())
        self.client.force_login(get_user_model().objects.create_superuser('msimamizi', password='x'))
        page = self.client.get(reverse('admin:crawler_source_changelist') + '?hali=hitilafu').content.decode()
        self.assertIn('Chanzo A', page)
        self.assertNotIn('Chanzo B', page)
        self.assertIn('badge-critical', page)


class StopTests(CrawlControlTestCase):
    def run_crawl(self, fake_crawl):
        with patch('crawler.management.commands.crawl.crawl_source', side_effect=fake_crawl), \
                patch('crawler.management.commands.crawl.settings.BASE_DIR', crawl_control.STATE_PATH.parent):
            call_command('crawl', source=['Chanzo A', 'Chanzo B'], stdout=open(os.devnull, 'w'))

    def test_stop_signal_ends_the_crawl_cleanly(self):
        import signal
        handler_before = signal.getsignal(signal.SIGTERM)
        reached = []

        def fake_crawl(source, **kwargs):
            reached.append(source.name)
            if source == self.first:
                return CrawlRun.objects.create(source=source, finished_at=timezone.now(), saved=2)
            CrawlRun.objects.create(source=source)  # Interrupted midway through this source.
            os.kill(os.getpid(), signal.SIGTERM)  # What the stop button sends.
            raise AssertionError('the crawl should have stopped before this line')

        self.run_crawl(fake_crawl)

        state = json.loads(crawl_control.STATE_PATH.read_text())
        self.assertTrue(state['stopped'])
        self.assertIsNotNone(state['finished_at'])
        interrupted = CrawlRun.objects.get(source=self.second)
        self.assertIsNotNone(interrupted.finished_at)
        self.assertEqual(interrupted.error_detail, crawl_control.STOPPED_NOTE)
        self.assertEqual(CrawlRun.objects.get(source=self.first).saved, 2)  # Earlier work is kept.
        self.assertEqual(signal.getsignal(signal.SIGTERM), handler_before)

        status = crawl_control.crawl_status()
        self.assertTrue(status['stopped'])
        self.assertEqual((status['done'], status['saved'], status['failed_sources']), (1, 2, 0))

    def test_stop_signals_the_crawl_process_and_waits_for_it(self):
        self.write_state(pid=4321)

        def finish(pid, sig):
            self.write_state(pid=4321, finished_at=timezone.now().isoformat(), stopped=True)

        with patch.object(crawl_control, '_process_alive', return_value=True), \
                patch.object(crawl_control.os, 'kill', side_effect=finish) as kill:
            self.assertTrue(crawl_control.stop_crawl())
        kill.assert_called_once_with(4321, crawl_control.signal.SIGTERM)

    def test_stop_button_shows_only_while_a_crawl_runs(self):
        self.client.force_login(self.staff)
        self.write_state()
        with patch.object(crawl_control, '_process_alive', return_value=True):
            page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Simamisha utafutaji</button>', page)
        self.assertNotIn('Tafuta habari mpya</button>', page)

        self.write_state(finished_at=timezone.now().isoformat(), stopped=True)
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('Simamisha utafutaji</button>', page)
        self.assertIn('Utafutaji wa mwisho ulisimamishwa', page)

    def test_stopping_when_nothing_runs_says_so(self):
        self.client.force_login(self.staff)
        with patch.object(crawl_control.os, 'kill') as kill:
            response = self.client.post(reverse('stop_crawl'), {'next': '/admin/'})
        kill.assert_not_called()
        self.assertEqual(response['Location'], '/admin/')
        page = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('Hakuna utafutaji unaoendelea.', page)

    def test_visitors_cannot_stop_a_crawl(self):
        self.write_state()
        with patch.object(crawl_control.os, 'kill') as kill:
            response = self.client.post(reverse('stop_crawl'))
        self.assertIn('/admin/login/', response['Location'])
        kill.assert_not_called()
