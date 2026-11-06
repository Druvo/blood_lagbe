/* Milestone celebration: turns the inline card into a focus-trapped dialog,
   dismissible with Esc, with canvas confetti unless the visitor prefers
   reduced motion. Shown once: the server records it as seen. */
(function () {
  'use strict';
  var root = document.querySelector('[data-mo-celebrate]');
  if (!root) { return; }

  var reduced = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var opener = document.activeElement;
  var raf = 0;

  function focusables() {
    return Array.prototype.slice.call(
      root.querySelectorAll('a[href], button:not([disabled])')
    ).filter(function (el) { return el.offsetParent !== null; });
  }

  function close() {
    cancelAnimationFrame(raf);
    document.removeEventListener('keydown', onKey, true);
    document.documentElement.classList.remove('mo-lock');
    root.classList.remove('is-overlay');
    root.removeAttribute('aria-modal');
    root.removeAttribute('role');
    root.style.display = 'none';
    if (opener && typeof opener.focus === 'function' && document.contains(opener)) {
      opener.focus();
    }
  }

  function onKey(event) {
    if (event.key === 'Escape' || event.key === 'Esc') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab') { return; }
    var items = focusables();
    if (!items.length) { event.preventDefault(); root.focus(); return; }
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === root)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function confetti(canvas) {
    var ctx = canvas.getContext && canvas.getContext('2d');
    if (!ctx) { return; }
    var width = canvas.width = canvas.offsetWidth;
    var height = canvas.height = canvas.offsetHeight;
    var colors = ['#d8213a', '#ff4d6d', '#f5a524', '#12b76a', '#2e90fa', '#7a5af8'];
    var pieces = [];
    for (var i = 0; i < 90; i++) {
      pieces.push({
        x: Math.random() * width,
        y: -Math.random() * height * 0.6,
        w: 6 + Math.random() * 6,
        h: 8 + Math.random() * 8,
        vy: 2 + Math.random() * 3,
        vx: -1 + Math.random() * 2,
        rot: Math.random() * Math.PI,
        vr: -0.15 + Math.random() * 0.3,
        color: colors[i % colors.length]
      });
    }
    var frames = 0;
    (function tick() {
      ctx.clearRect(0, 0, width, height);
      pieces.forEach(function (p) {
        p.x += p.vx; p.y += p.vy; p.rot += p.vr;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      });
      frames += 1;
      if (frames < 260) { raf = requestAnimationFrame(tick); }
      else { ctx.clearRect(0, 0, width, height); }
    })();
  }

  root.classList.add('is-overlay');
  document.documentElement.classList.add('mo-lock');
  document.addEventListener('keydown', onKey, true);

  Array.prototype.forEach.call(root.querySelectorAll('[data-mo-close]'), function (btn) {
    btn.addEventListener('click', close);
  });
  // Clicking the dim backdrop (not the card) also dismisses.
  root.addEventListener('mousedown', function (event) {
    if (event.target === root) { close(); }
  });

  var items = focusables();
  (items.length ? items[0] : root).focus();

  var canvas = root.querySelector('[data-mo-confetti]');
  if (canvas && !reduced) { confetti(canvas); }
})();
