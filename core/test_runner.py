"""Test runner that starts every test with an empty cache.

Rate limits and cached statistics live in the cache, which is shared by the
whole test process. Without this, a test that fills a counter (failed logins,
join attempts) would change the outcome of whichever test runs after it.
"""
import unittest

from django.core.cache import cache
from django.test.runner import DiscoverRunner


def _cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _cases(item)
        else:
            yield item


def _clear_first(test):
    original = test.setUp

    def set_up():
        cache.clear()
        original()

    test.setUp = set_up


class CacheIsolatedRunner(DiscoverRunner):
    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)
        for test in _cases(suite):
            _clear_first(test)
        return suite
