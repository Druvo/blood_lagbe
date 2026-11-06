from django import template
from django.db.models import Count, Q
from django.utils import timezone

from events import awards
from events.models import (
    DEFAULT_DURATION,
    Campaign,
    CampaignBadgeAward,
    Event,
    EventRSVP,
)

register = template.Library()


# Extension points. The shared templates call these three tags; each
# renders the matching template. The request panel stays empty on purpose.


def _user(context):
    request = context.get('request')
    user = getattr(request, 'user', None) or context.get('user')
    return user if user is not None and user.is_authenticated else None


def _live(now):
    return (
        Q(event__ends_at__gte=now)
        | Q(event__ends_at__isnull=True,
            event__starts_at__gte=now - DEFAULT_DURATION)
    )


@register.inclusion_tag('events/_hub.html', takes_context=True)
def events_hub(context):
    """Panel on the signed-in donor hub (My hub): next RSVP, attendance,
    campaign badges and a link to host a drive."""
    user = _user(context)
    if user is None:
        return {}
    awards.sync_user(user)
    now = timezone.now()
    next_rsvp = (
        EventRSVP.objects.filter(
            user=user, event__status=Event.Status.APPROVED,
        ).filter(_live(now))
        .select_related('event').order_by('event__starts_at').first()
    )
    attended = EventRSVP.objects.filter(user=user, checked_in=True).aggregate(
        total=Count('pk'), verified=Count('pk', filter=Q(verified=True)),
    )
    badges = list(
        CampaignBadgeAward.objects.filter(user=user).select_related('campaign')[:6]
    )
    return {
        'next_rsvp': next_rsvp,
        'attended': attended['total'],
        'attended_verified': attended['verified'],
        'badges': badges,
    }


@register.inclusion_tag('events/_home.html', takes_context=True)
def events_home(context):
    """Section on the public home page: an active campaign banner and the
    next three approved drives."""
    campaigns = Campaign.active_on()
    events = list(Event.objects.with_stats().upcoming()[:3])
    if not campaigns and not events:
        return {}
    return {'campaign': campaigns[0] if campaigns else None, 'events': events}


@register.inclusion_tag('events/_request.html', takes_context=True)
def events_request(context):
    """Panel on a blood request page. Nothing to show here."""
    return {}
