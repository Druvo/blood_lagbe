"""Small cache-based abuse limits (login guessing, password-reset mail).

Counters live in the Django cache, so they are per process unless a shared
cache (Redis, Memcached) is configured; see the README. Identifiers such as
phone numbers are hashed before they become part of a cache key, so they
never show up in a cache dump or a log line.
"""
import hashlib

from django.core.cache import cache

WINDOW_SECONDS = 15 * 60


def client_ip(request):
    """The peer address. X-Forwarded-For is deliberately ignored: it is
    client-controlled unless a trusted proxy rewrites it."""
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def _key(scope, ident):
    digest = hashlib.sha256(str(ident).encode('utf-8')).hexdigest()[:32]
    return f'rl:{scope}:{digest}'


def count(scope, ident):
    return cache.get(_key(scope, ident), 0)


def is_limited(scope, ident, limit):
    return count(scope, ident) >= limit


def hit(scope, ident, window=WINDOW_SECONDS):
    """Record one event; the window starts at the first event."""
    key = _key(scope, ident)
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        return 1


def reset(scope, ident):
    cache.delete(_key(scope, ident))
