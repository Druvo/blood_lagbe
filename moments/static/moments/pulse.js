/* Live pledge counter: polls a counts-only JSON endpoint every 30 seconds
   and updates an aria-live line. Pauses while the tab is hidden. */
(function () {
  'use strict';
  var line = document.querySelector('[data-mo-pulse]');
  if (!line || !window.fetch) { return; }

  var url = line.getAttribute('data-url');
  var needed = parseInt(line.getAttribute('data-needed'), 10) || 0;
  var INTERVAL = 30000;
  var timer = null;
  var lastRun = Date.now();
  var delay = INTERVAL;

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  function render(data) {
    var bags = plural(needed, 'bag', 'bags');
    var text;
    if (!data.people) {
      text = 'Nobody has offered yet. ' + bags + ' still needed.';
    } else {
      text = plural(data.people, 'person', 'people') + ' responding · ' +
        data.bags_pledged + ' of ' + bags + ' offered · ' +
        data.still_needed + ' still needed';
    }
    // Only touch the DOM when the words change, so screen readers do not
    // repeat the same announcement every poll.
    if (line.textContent !== text) { line.textContent = text; }
  }

  function stop() { if (timer) { clearTimeout(timer); timer = null; } }

  function schedule() {
    stop();
    if (document.hidden) { return; }
    timer = setTimeout(poll, delay);
  }

  function poll() {
    lastRun = Date.now();
    fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (res) {
        if (res.status === 429) { delay = INTERVAL * 2; return null; }
        if (!res.ok) { return null; }
        delay = INTERVAL;
        return res.json();
      })
      .then(function (data) {
        if (!data) { schedule(); return; }
        render(data);
        if (data.open === false) { stop(); return; }
        schedule();
      })
      .catch(schedule);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); return; }
    if (Date.now() - lastRun >= INTERVAL) { poll(); } else { schedule(); }
  });

  schedule();
})();
