{% load static %}/* {{ SITE_NAME }} service worker.
 *
 * Deliberately tiny. It does two things only:
 *   1. keeps the offline page and the core CSS/JS so the app shell opens fast;
 *   2. shows the offline page when a page navigation fails (no network).
 * It never stores HTML pages, API/JSON responses or anything behind a login.
 * Pages and data always come from the network.
 */
var CACHE = 'bb-shell-v2';
var OFFLINE_URL = '{% url "offline" %}';
var STATIC_PREFIX = '{% get_static_prefix %}';
var PRECACHE = [
  OFFLINE_URL,
  '{% static "css/bloodbond.css" %}',
  '{% static "css/pages.css" %}',
  '{% static "css/feather.css" %}',
  '{% static "js/bloodbond.js" %}',
  '{% static "images/brand/bloodbond-mark.svg" %}'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      // One missing file must not block installing the rest.
      return Promise.all(PRECACHE.map(function (url) {
        return cache.add(url).catch(function () { /* ignore */ });
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (key) { return key !== CACHE; })
        .map(function (key) { return caches.delete(key); }));
    }).then(function () { return self.clients.claim(); })
  );
});

function isStaticAsset(url) {
  return url.pathname.indexOf(STATIC_PREFIX) === 0 &&
    /\.(css|js|svg|png|woff2?|ttf)$/i.test(url.pathname);
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    // Network only; fall back to the offline page when it fails.
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(OFFLINE_URL).then(function (cached) {
          return cached || new Response('You are offline.', {
            status: 503, headers: { 'Content-Type': 'text/plain' }
          });
        });
      })
    );
    return;
  }

  if (isStaticAsset(url)) {
    // Network first, so CSS/JS are never stale; the cached copy is only a
    // fallback for when the network is down.
    event.respondWith(
      fetch(request).then(function (response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      }).catch(function () { return caches.match(request); })
    );
  }
  // Everything else (forms, JSON, media, other origins): not intercepted.
});
