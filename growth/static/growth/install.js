/* "Add to Home Screen" card.
   - Chromium: uses the beforeinstallprompt event.
   - iOS Safari: a short hint, since iOS has no install event.
   Shown only after a first action (signed in, or 3 pages visited), never
   when already installed, and not again for 30 days after it is dismissed.
   Storage is wrapped in try/catch: it can be blocked or full. */
(function () {
  'use strict';
  if (window.__bbInstallInit) return;
  window.__bbInstallInit = true;

  var script = document.currentScript || document.querySelector('script[src*="growth/install.js"]');
  var siteName = (script && script.getAttribute('data-site')) || 'this app';
  var authed = !!(script && script.getAttribute('data-authed') === '1');

  var DISMISS_KEY = 'bb-install-dismissed';
  var PAGES_KEY = 'bb-install-pages';
  var DAY = 24 * 60 * 60 * 1000;
  var COOLDOWN = 30 * DAY;
  var PAGES_NEEDED = 3;

  function read(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function write(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* ignore */ } }

  function isStandalone() {
    try {
      if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return true;
    } catch (e) { /* ignore */ }
    return window.navigator.standalone === true;
  }

  function recentlyDismissed() {
    var at = parseInt(read(DISMISS_KEY), 10);
    return !isNaN(at) && Date.now() - at < COOLDOWN;
  }

  function dismiss() { write(DISMISS_KEY, String(Date.now())); }

  // Count this page view (once per page load).
  var pages = parseInt(read(PAGES_KEY), 10) || 0;
  pages += 1;
  write(PAGES_KEY, String(pages));

  function hadFirstAction() { return authed || pages >= PAGES_NEEDED; }

  if (isStandalone() || recentlyDismissed()) return;

  var ua = window.navigator.userAgent || '';
  var isIos = /iPad|iPhone|iPod/.test(ua) ||
    (window.navigator.platform === 'MacIntel' && window.navigator.maxTouchPoints > 1);
  var isIosSafari = isIos && /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);

  var deferred = null;
  var card = null;

  function build(message, withInstall) {
    if (card) return;
    card = document.createElement('div');
    card.className = 'gr-install';
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Install ' + siteName);

    var text = document.createElement('p');
    var strong = document.createElement('strong');
    strong.textContent = 'Add ' + siteName + ' to your home screen';
    text.appendChild(strong);
    text.appendChild(document.createElement('br'));
    text.appendChild(document.createTextNode(message));
    card.appendChild(text);

    var row = document.createElement('div');
    row.className = 'gr-install__row';
    if (withInstall) {
      var install = document.createElement('button');
      install.type = 'button';
      install.className = 'bb-btn bb-btn--primary bb-btn--sm';
      install.textContent = 'Install';
      install.addEventListener('click', function () {
        if (!deferred) { remove(); return; }
        deferred.prompt();
        var choice = deferred.userChoice;
        deferred = null;
        if (choice && choice.then) {
          choice.then(function (r) {
            if (!r || r.outcome !== 'accepted') dismiss();
            remove();
          }, remove);
        } else { remove(); }
      });
      row.appendChild(install);
    }
    var later = document.createElement('button');
    later.type = 'button';
    later.className = 'bb-btn bb-btn--ghost bb-btn--sm';
    later.textContent = withInstall ? 'Not now' : 'Got it';
    later.addEventListener('click', function () { dismiss(); remove(); });
    row.appendChild(later);
    card.appendChild(row);
    document.body.appendChild(card);
  }

  function remove() {
    if (card && card.parentNode) card.parentNode.removeChild(card);
    card = null;
  }

  function maybeShow() {
    if (card || !hadFirstAction() || isStandalone() || recentlyDismissed()) return;
    if (deferred) {
      build('Open it like an app, even on a slow connection.', true);
    } else if (isIosSafari) {
      build('Tap the Share button, then "Add to Home Screen".', false);
    }
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferred = e;
    maybeShow();
  });

  window.addEventListener('appinstalled', function () {
    dismiss();
    deferred = null;
    remove();
  });

  if (isIosSafari) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', maybeShow);
    } else { maybeShow(); }
  }
})();
