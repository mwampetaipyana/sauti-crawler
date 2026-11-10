// Keeps the crawl panel current: every few seconds while a crawl runs, and now and then while idle,
// so a crawl started from another page or by another editor still shows up here.
(function () {
  const holder = document.getElementById('crawl-panel');
  const chip = document.getElementById('crawl-chip');
  const announce = document.getElementById('crawl-announce');
  const RUNNING_DELAY = 4000;
  const IDLE_DELAY = 20000;

  // Buttons ask before stopping and show that a start is under way. Bound on the document,
  // because the panel's markup is replaced on every refresh.
  document.addEventListener('submit', (event) => {
    if (!event.target.closest('.crawl-panel')) return;
    const button = event.submitter || event.target.querySelector('button[type="submit"]');
    if (!button) return;
    if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (button.dataset.busyLabel) {
      button.textContent = button.dataset.busyLabel;
      button.disabled = true;
    }
  });

  if (!holder) return;
  const here = window.location.pathname + window.location.search;
  const url = holder.dataset.url + '?next=' + encodeURIComponent(here);
  let wasRunning = holder.querySelector('[data-running="true"]') !== null;

  function say(text) {
    // Only changes of state are announced, not every progress update.
    if (announce) announce.textContent = text;
  }

  async function refresh() {
    if (!document.hidden) {
      try {
        const response = await fetch(url + (wasRunning ? '&was_running=1' : ''), {credentials: 'same-origin'});
        if (!response.ok || response.redirected) return;  // Signed out: stop asking.
        const html = await response.text();
        const running = html.includes('data-running="true"');
        // An idle panel is left alone, so its "finished" note stays and nothing moves under the pointer.
        if (running || wasRunning) holder.innerHTML = html;
        if (running && !wasRunning) say('Utafutaji wa habari mpya umeanza.');
        if (wasRunning && !running) {
          const done = holder.querySelector('.crawl-done');
          say(done ? done.textContent.replace(/\s+/g, ' ').trim() : 'Utafutaji umekwisha.');
        }
        if (chip) chip.hidden = !running;
        wasRunning = running;
      } catch (error) {
        // The server may be restarting; try again on the next tick.
      }
    }
    setTimeout(refresh, wasRunning ? RUNNING_DELAY : IDLE_DELAY);
  }
  setTimeout(refresh, wasRunning ? RUNNING_DELAY : IDLE_DELAY);
})();
