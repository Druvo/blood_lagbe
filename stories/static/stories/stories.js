(function () {
  'use strict';

  // Reactions: submit in place, fall back to the normal POST + redirect.
  document.addEventListener('submit', function (e) {
    var form = e.target.closest('form[data-story-react]');
    if (!form || !window.fetch) return;
    e.preventDefault();
    var btn = form.querySelector('.bbs-react');
    btn.disabled = true;
    fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' }
    }).then(function (r) {
      if (r.redirected || !r.ok) throw new Error('fallback');
      return r.json();
    }).then(function (data) {
      btn.classList.toggle('is-on', !!data.active);
      btn.setAttribute('aria-pressed', data.active ? 'true' : 'false');
      var n = btn.querySelector('.bbs-react__n');
      if (n) n.textContent = data.count;
      btn.disabled = false;
    }).catch(function () {
      form.removeAttribute('data-story-react');
      form.submit();
    });
  });

  // Web Share API: the button is hidden until the browser can actually share.
  document.querySelectorAll('[data-story-share]').forEach(function (btn) {
    if (!navigator.share) return;
    btn.hidden = false;
    btn.addEventListener('click', function () {
      navigator.share({
        title: btn.getAttribute('data-title'),
        text: btn.getAttribute('data-text'),
        url: btn.getAttribute('data-url')
      }).catch(function () { /* user closed the sheet */ });
    });
  });

  // Character counter on the story form.
  document.querySelectorAll('[data-count-for]').forEach(function (out) {
    var field = document.getElementById(out.getAttribute('data-count-for'));
    if (!field) return;
    var max = parseInt(field.getAttribute('maxlength'), 10);
    function update() {
      var left = max - field.value.length;
      out.textContent = left + ' characters left';
      out.classList.toggle('is-near', left < 100);
    }
    field.addEventListener('input', update);
    update();
  });
})();
