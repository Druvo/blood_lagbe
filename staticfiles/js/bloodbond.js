/* BloodBond: tiny, dependency-free UI behaviour. */
(function () {
  'use strict';

  // Account / overflow menus.
  document.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-menu-toggle]');
    document.querySelectorAll('.bb-menu.is-open').forEach(function (menu) {
      if (!toggle || !menu.contains(toggle)) {
        menu.classList.remove('is-open');
        var btn = menu.querySelector('[data-menu-toggle]');
        if (btn) btn.setAttribute('aria-expanded', 'false');
      }
    });
    if (toggle) {
      var menu = toggle.closest('.bb-menu');
      var open = menu.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      document.querySelectorAll('.bb-menu.is-open').forEach(function (m) { m.classList.remove('is-open'); });
    }
  });

  // Theme toggle: remembered per browser, follows the system by default.
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem('bb-theme');
    if (saved) root.setAttribute('data-theme', saved);
  } catch (err) { /* storage can be blocked */ }
  document.addEventListener('click', function (e) {
    if (!e.target.closest('[data-theme-toggle]')) return;
    var dark = root.getAttribute('data-theme') === 'dark' ||
      (!root.getAttribute('data-theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    var next = dark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('bb-theme', next); } catch (err) { /* ignore */ }
  });

  // Count-up numbers: <span data-count="1234">0</span>
  var counters = document.querySelectorAll('[data-count]');
  function run(el) {
    var target = parseInt(el.getAttribute('data-count'), 10) || 0;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || target < 2) { el.textContent = target.toLocaleString(); return; }
    var start = null, dur = 900;
    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3))).toLocaleString();
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { run(en.target); io.unobserve(en.target); } });
    });
    counters.forEach(function (el) { io.observe(el); });
  } else { counters.forEach(run); }

  // Copy buttons: <button data-copy="text or #selector">
  var toast;
  window.bbToast = function (msg) {
    if (!toast) { toast = document.createElement('div'); toast.className = 'bb-toast'; toast.setAttribute('role', 'status'); document.body.appendChild(toast); }
    toast.textContent = msg; toast.classList.add('is-show');
    clearTimeout(toast._t); toast._t = setTimeout(function () { toast.classList.remove('is-show'); }, 2200);
  };
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-copy]');
    if (!btn) return;
    var raw = btn.getAttribute('data-copy');
    var src = raw.charAt(0) === '#' ? document.querySelector(raw) : null;
    var text = src ? (src.value || src.textContent) : raw;
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { window.bbToast('Copied'); });
  });


  // Staggered reveal for grids of cards. CSS hides them only when html.js is
  // set; if anything here fails the class is removed and content stays visible.
  window.__bbJs = true;
  (function () {
    var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var nodes = document.querySelectorAll('.bb-cols-2 > *, .bb-cols-3 > *, .bb-cols-4 > *, .bb-cols-auto > *, .bb-strip > *, [data-reveal]');
    if (reduce || !('IntersectionObserver' in window)) { root.classList.remove('js'); return; }
    var seen = new WeakMap();
    var ro = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target;
        ro.unobserve(el);
        var siblings = el.parentNode ? Array.prototype.indexOf.call(el.parentNode.children, el) : 0;
        el.style.setProperty('--rv-i', Math.min(siblings, 6));
        el.classList.add('is-in');
        setTimeout(function () { el.classList.add('rv-done'); el.classList.remove('is-in'); }, 900);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.05 });
    nodes.forEach(function (el) { ro.observe(el); });
  })();

  // Auto-dismiss flash messages.
  document.querySelectorAll('.bb-messages .bb-alert').forEach(function (el) {
    setTimeout(function () { el.style.transition = 'opacity .4s'; el.style.opacity = '0'; setTimeout(function () { el.remove(); }, 450); }, 7000);
  });
})();
