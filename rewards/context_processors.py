from django.conf import settings
from django.utils.translation import ngettext


def donation_interval(request):
    days = settings.DONATION_INTERVAL_DAYS
    months = round(days / 30)
    return {
        'donation_interval_days': days,
        'donation_interval_label': (
            ngettext('%(n)s month', '%(n)s months', months) % {'n': months}
            if days % 30 == 0
            else ngettext('%(n)s day', '%(n)s days', days) % {'n': days}
        ),
    }


def contact_inbox(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    return {
        'contact_pending_count': user.contact_requests_received.filter(
            status='pending',
        ).count(),
    }
