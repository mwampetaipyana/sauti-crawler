"""Start a crawl from the web interface and report its progress.

The crawl runs as its own process, the same `manage.py crawl` used from the terminal or cron,
so it keeps going if the web server restarts or the browser tab closes. The crawl command
records what it is doing in a small state file, which the dashboard reads.
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

LOCK_PATH = settings.BASE_DIR / '.crawl.lock'
STATE_PATH = settings.BASE_DIR / '.crawl-state.json'
LOG_PATH = settings.BASE_DIR / 'crawl.log'
LOG_MAX_BYTES = 5 * 1024 * 1024
START_WAIT_SECONDS = 3
STOP_WAIT_SECONDS = 10
STOPPED_NOTE = 'Ilisimamishwa na mtumiaji.'


class CrawlAlreadyRunning(Exception):
    pass


class CrawlFailedToStart(Exception):
    pass


class CrawlNotRunning(Exception):
    pass


class CrawlStopped(BaseException):
    """Raised inside a crawl asked to stop. Not an Exception, so no error handler can swallow it."""


def write_state(state):
    temporary = STATE_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    os.replace(temporary, STATE_PATH)


def read_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return None


def _process_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # After a reboot the number may belong to another program, so check it is a crawl.
    cmdline = f'/proc/{pid}/cmdline'
    if os.path.exists(cmdline):
        with open(cmdline, 'rb') as handle:
            return b'crawl' in handle.read()
    return True


def lock_is_held():
    """True while any crawl holds the lock, including one started by cron or in a terminal."""
    try:
        handle = open(LOCK_PATH, 'a')
    except OSError:
        return False
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def _rotate_log():
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
        os.replace(LOG_PATH, LOG_PATH.with_suffix('.log.1'))


def start_crawl():
    """Launch a crawl of every enabled source in the background."""
    if lock_is_held():
        raise CrawlAlreadyRunning
    _rotate_log()
    with open(LOG_PATH, 'a') as log:
        log.write(f'\n--- Crawl started from the dashboard at {timezone.localtime():%Y-%m-%d %H:%M} ---\n')
        log.flush()
        log_start = log.tell()
        process = subprocess.Popen(
            [sys.executable, str(settings.BASE_DIR / 'manage.py'), 'crawl'],
            cwd=settings.BASE_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,  # Stopping the web server must not stop the crawl.
        )
    # Collect its exit status when it ends, so it doesn't linger as a zombie process.
    threading.Thread(target=process.wait, daemon=True).start()

    # Wait briefly so a crawl that can't start at all is reported now rather than looking stuck.
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            if process.returncode:
                raise CrawlFailedToStart(_last_log_line(log_start))
            return
        state = read_state()
        if state and state.get('pid') == process.pid:
            return
        time.sleep(0.2)


def stop_crawl():
    """Ask the running crawl to stop. Returns True once it has, False if it is still finishing."""
    state = read_state()
    if not state or state.get('finished_at') or not _process_alive(state['pid']):
        raise CrawlNotRunning
    os.kill(state['pid'], signal.SIGTERM)  # The crawl command turns this into CrawlStopped.

    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        current = read_state() or {}
        if current.get('finished_at') or not _process_alive(state['pid']):
            return True
        time.sleep(0.2)
    return False


def _last_log_line(offset):
    """The last line the crawl itself wrote, which explains why it stopped."""
    try:
        with open(LOG_PATH, 'rb') as handle:
            handle.seek(offset)
            text = handle.read().decode(errors='replace')
    except FileNotFoundError:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:300] if lines else ''


def crawl_status():
    """What the dashboard shows: the crawl in progress, or a summary of the last one."""
    state = read_state()
    if not state:
        return {'running': lock_is_held(), 'started_at': None}

    from crawler.models import CrawlRun

    started_at = datetime.fromisoformat(state['started_at'])
    finished_at = datetime.fromisoformat(state['finished_at']) if state.get('finished_at') else None
    running = finished_at is None and _process_alive(state['pid'])
    sources = state.get('sources', [])

    runs = CrawlRun.objects.filter(started_at__gte=started_at, source__name__in=sources)
    # A source cut short by the stop button wasn't fully read, so it doesn't count as done.
    done = runs.exclude(finished_at=None).exclude(error_detail=STOPPED_NOTE)
    totals = done.aggregate(saved=Sum('saved'), refreshed=Sum('refreshed'), flagged=Sum('flagged'))
    current = runs.filter(finished_at=None).select_related('source').first()
    return {
        'running': running,
        'started_at': started_at,
        'finished_at': finished_at,
        'stopped_early': not running and finished_at is None,
        'total': len(sources),
        'done': done.count(),
        'current': current.source.name if running and current else '',
        'saved': totals['saved'] or 0,
        'refreshed': totals['refreshed'] or 0,
        'flagged': totals['flagged'] or 0,
        'stopped': bool(state.get('stopped')),
        'failed_sources': done.exclude(error_detail='').filter(saved=0).count(),
    }
