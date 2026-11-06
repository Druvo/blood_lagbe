/* Happening now: refreshes the anonymous feed once a minute. Without this
   file the server-rendered list simply stays as it is. */
(function () {
  'use strict';
  var box = document.querySelector('[data-sp-pulse]');
  if (!box || !window.fetch) { return; }
  var list = box.querySelector('[data-sp-list]');
  var url = box.getAttribute('data-url');
  var every = (parseInt(box.getAttribute('data-refresh'), 10) || 60) * 1000;
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var timer = null;
  var last = '';

  function row(item, fresh) {
    var li = document.createElement('li');
    li.className = 'sp-pulse__item' + (fresh && !calm ? ' sp-new' : '');
    var icon = document.createElement('i');
    icon.className = 'feather-' + String(item.icon).replace(/[^a-z-]/g, '');
    icon.setAttribute('aria-hidden', 'true');
    var text = document.createElement('span');
    text.className = 'sp-pulse__text';
    text.textContent = item.text;
    var when = document.createElement('span');
    when.className = 'sp-pulse__when';
    when.textContent = item.when;
    li.appendChild(icon); li.appendChild(text); li.appendChild(when);
    return li;
  }

  function render(data) {
    if (!data || !data.show) { box.hidden = true; return; }
    box.hidden = false;
    var key = data.events.map(function (e) { return e.text + '|' + e.when; }).join('\n');
    if (key === last) { return; }
    var firstRun = last === '';
    last = key;
    while (list.firstChild) { list.removeChild(list.firstChild); }
    data.events.forEach(function (item) { list.appendChild(row(item, !firstRun)); });
  }

  function refresh() {
    if (document.hidden) { return; }
    fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(render)
      .catch(function () { /* keep what is shown */ });
  }

  function start() { if (!timer) { timer = setInterval(refresh, every); } }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); } else { refresh(); start(); }
  });
  start();
})();
