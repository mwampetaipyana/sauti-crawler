import fcntl
import os
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crawler.models import CrawlRun, Source
from crawler.services.crawl_control import STOPPED_NOTE, CrawlStopped, write_state
from crawler.services.fetcher import PoliteFetcher
from crawler.services.pipeline import crawl_source


class Command(BaseCommand):
    help = 'Crawl enabled sources once, or repeatedly with --loop.'

    def add_arguments(self, parser):
        parser.add_argument('--source', action='append', help='Crawl only this source name. Repeatable.')
        parser.add_argument('--limit', type=int, help='New articles to fetch per source, overriding its setting.')
        parser.add_argument('--loop', action='store_true', help='Keep crawling until stopped.')
        parser.add_argument('--interval', type=int, default=3600, help='Seconds between crawls with --loop.')

    def handle(self, *args, **options):
        # Two overlapping crawls would double the load on every site, so only one may run.
        lock_file = open(settings.BASE_DIR / '.crawl.lock', 'w')
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CommandError('Another crawl is already running.')

        # The dashboard's stop button sends SIGTERM; stop cleanly instead of dying mid-write.
        previous_handler = signal.signal(signal.SIGTERM, _stop_requested)
        with lock_file:
            try:
                while True:
                    self.crawl_once(options)
                    if not options['loop']:
                        return
                    self.stdout.write(f'Next crawl in {options["interval"]} seconds.')
                    time.sleep(options['interval'])
            except CrawlStopped:
                self.stdout.write('Crawl stopped on request.')
            finally:
                signal.signal(signal.SIGTERM, previous_handler)

    def crawl_once(self, options):
        sources = Source.objects.filter(enabled=True)
        if options['source']:
            sources = Source.objects.filter(name__in=options['source'])
            missing = set(options['source']) - set(sources.values_list('name', flat=True))
            if missing:
                raise CommandError(f'Unknown source: {", ".join(sorted(missing))}')
        if not sources:
            raise CommandError('No sources to crawl. Run `manage.py seed_crawler` first.')

        sources = list(sources)
        # The dashboard reads this file to show the crawl's progress.
        state = {
            'pid': os.getpid(),
            'started_at': timezone.now().isoformat(),
            'finished_at': None,
            'sources': [source.name for source in sources],
        }
        write_state(state)

        # One fetcher per crawl, so pacing and robots.txt rules carry across sources sharing a host.
        fetcher = PoliteFetcher()
        try:
            for source in sources:
                self.stdout.write(f'Crawling {source.name}...')
                run = crawl_source(source, fetcher=fetcher, limit=options['limit'])
                self.stdout.write(
                    f'  found {run.discovered}, saved {run.saved}, updated {run.refreshed}, flagged {run.flagged}, '
                    f'duplicates {run.duplicates}, skipped {run.skipped}, '
                    f'blocked by robots.txt {run.blocked_by_robots}, errors {run.errors}'
                )
                if run.error_detail:
                    self.stderr.write(f'  last error: {run.error_detail.splitlines()[-1]}')
                self.stdout.flush()
        except CrawlStopped:
            # Close the source that was interrupted, so it doesn't look as if it is still running.
            CrawlRun.objects.filter(finished_at=None, started_at__gte=state['started_at']).update(
                finished_at=timezone.now(), error_detail=STOPPED_NOTE)
            state['stopped'] = True
            raise
        finally:
            state['finished_at'] = timezone.now().isoformat()
            write_state(state)


def _stop_requested(signum, frame):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # One request is enough; let the cleanup finish.
    raise CrawlStopped
