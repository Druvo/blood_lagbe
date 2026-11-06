"""Public service other lanes call to put a message in someone's inbox."""
from django.db import IntegrityError, transaction
from django.utils import timezone

from alerts.models import Notification


def _clip(text, limit):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _safe_url(url):
    """Only a site-relative path may be linked: never another site, never a
    scheme such as javascript:."""
    url = (url or '').strip()
    if url.startswith('/') and not url.startswith('//') and '\\' not in url:
        return url[:300]
    return ''


def notify_once(user, kind, title, body='', url=None, dedupe_key=None):
    """Create a notification. Returns (notification, created).

    With a ``dedupe_key`` the same key for the same user returns the existing
    notification instead of adding another, so retries and double signals are
    harmless. Keep contact details and patient details out of the text.
    """
    fields = {
        'kind': _clip(kind, 30) or Notification.Kind.INFO,
        'title': _clip(title, 200),
        'body': _clip(body, 1000),
        'url': _safe_url(url),
    }
    if not dedupe_key:
        return Notification.objects.create(user=user, **fields), True
    dedupe_key = _clip(dedupe_key, 120)
    try:
        with transaction.atomic():
            return Notification.objects.get_or_create(
                user=user, dedupe_key=dedupe_key, defaults=fields,
            )
    except IntegrityError:  # lost a race with another writer
        return Notification.objects.get(user=user, dedupe_key=dedupe_key), False


def notify(user, kind, title, body, url=None, dedupe_key=None):
    """Add an in-app notification for ``user`` and return it (idempotent when
    ``dedupe_key`` repeats for the same user)."""
    return notify_once(user, kind, title, body, url, dedupe_key)[0]


def unread_count(user):
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_all_read(user):
    return Notification.objects.filter(
        user=user, read_at__isnull=True,
    ).update(read_at=timezone.now())
