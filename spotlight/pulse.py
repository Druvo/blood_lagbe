"""Happening now: a short, anonymous feed of real recent events.

Each event names only a district and a blood group, never a person, hospital,
note or patient detail. Rules:

* only real events: a pledge ("I can help"), a request closed as fulfilled,
  a donation an admin verified;
* an event is held back until it is at least ``HOLD_BACK_MINUTES`` old;
* flagged requests are skipped, and so is anything done by a member who has
  opted out of the leaderboard;
* the raw list is cached for ``CACHE_SECONDS``; opt-outs are re-checked each
  time it is used, so a member who just hid themselves drops out at once.

Public helpers for other lanes: ``pulse()``.
"""
import datetime

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext, ngettext

from accounts.models import Profile
from rewards.models import BloodRequest, Donation, RequestResponse

HOLD_BACK_MINUTES = 10
WINDOW_DAYS = 14
MAX_EVENTS = 8
MIN_EVENTS_TO_SHOW = 3
CACHE_SECONDS = 60
CACHE_KEY = 'spotlight:pulse:raw'
FETCH_PER_SOURCE = 16

PLEDGE, FULFILLED, DONATED = 'pledge', 'fulfilled', 'donation'
ICONS = {PLEDGE: 'thumbs-up', FULFILLED: 'check-circle', DONATED: 'droplet'}


def _flagged_ids():
    """Ids of requests with enough open reports to be hidden publicly."""
    return BloodRequest.objects.flagged().values('pk')


def _collect(now):
    """Raw events, newest first. Each is a dict of plain values: kind, at,
    district, group and uid (the member the event belongs to, used only to
    re-check privacy; it is never sent out)."""
    cutoff = now - datetime.timedelta(minutes=HOLD_BACK_MINUTES)
    since = now - datetime.timedelta(days=WINDOW_DAYS)
    events = []

    pledges = (
        RequestResponse.objects.filter(
            created_at__lte=cutoff, created_at__gte=since,
            donor__profile__show_on_leaderboard=True,
        ).exclude(blood_request__in=_flagged_ids())
        .select_related('blood_request').order_by('-created_at')
        [:FETCH_PER_SOURCE]
    )
    for item in pledges:
        events.append({
            'kind': PLEDGE, 'at': item.created_at, 'uid': item.donor_id,
            'district': item.blood_request.district,
            'group': item.blood_request.blood_group,
        })

    fulfilled = (
        BloodRequest.objects.filter(
            status=BloodRequest.Status.CLOSED,
            outcome=BloodRequest.Outcome.FULFILLED_HERE,
            closed_at__lte=cutoff, closed_at__gte=since,
            requester__profile__show_on_leaderboard=True,
        ).exclude(pk__in=_flagged_ids()).order_by('-closed_at')
        [:FETCH_PER_SOURCE]
    )
    for item in fulfilled:
        events.append({
            'kind': FULFILLED, 'at': item.closed_at, 'uid': item.requester_id,
            'district': item.district, 'group': item.blood_group,
        })

    donations = (
        Donation.objects.filter(
            status=Donation.Status.APPROVED,
            updated_at__lte=cutoff, updated_at__gte=since,
            donor__profile__show_on_leaderboard=True,
        ).order_by('-updated_at')[:FETCH_PER_SOURCE]
    )
    for item in donations:
        events.append({
            'kind': DONATED, 'at': item.updated_at, 'uid': item.donor_id,
            'district': item.district, 'group': item.blood_group,
        })

    events.sort(key=lambda e: e['at'], reverse=True)
    return events


def raw_events(now=None):
    """The cached raw list (see the module docstring)."""
    events = cache.get(CACHE_KEY)
    if events is None:
        events = _collect(now or timezone.now())
        cache.set(CACHE_KEY, events, CACHE_SECONDS)
    return events


def relative_time(then, now=None):
    """A rounded phrase such as "about 2 hours ago". Never exact."""
    now = now or timezone.now()
    minutes = max(int((now - then).total_seconds() // 60), 0)
    if minutes < 55:
        n = max(5 * round(minutes / 5), HOLD_BACK_MINUTES)
        return gettext('about %(n)s minutes ago') % {'n': n}
    hours = round(minutes / 60)
    if hours < 24:
        return ngettext(
            'about %(n)s hour ago', 'about %(n)s hours ago', hours,
        ) % {'n': hours}
    days = round(hours / 24)
    return ngettext(
        'about %(n)s day ago', 'about %(n)s days ago', days,
    ) % {'n': days}


def event_text(event):
    """The one-line, anonymous sentence for a raw event."""
    group, district = event['group'], event['district']
    if event['kind'] == PLEDGE:
        return gettext(
            'A donor in %(district)s offered to give %(group)s'
        ) % {'district': district, 'group': group}
    if event['kind'] == FULFILLED:
        # "An A+ / An O+ / An AB+" but "A B+": the article follows the sound.
        if group[:1] in ('A', 'O'):
            return gettext(
                'An %(group)s request in %(district)s was fulfilled'
            ) % {'district': district, 'group': group}
        return gettext(
            'A %(group)s request in %(district)s was fulfilled'
        ) % {'district': district, 'group': group}
    if district:
        return gettext(
            'A donor in %(district)s completed a verified donation'
        ) % {'district': district}
    return gettext('A donor completed a verified donation')


def pulse(now=None):
    """Up to MAX_EVENTS anonymous events: dicts with kind, icon, text and
    when. Empty list when there is nothing real to say."""
    now = now or timezone.now()
    events = raw_events(now)
    hidden = set(Profile.objects.filter(
        user_id__in={e['uid'] for e in events}, show_on_leaderboard=False,
    ).values_list('user_id', flat=True))
    result = []
    for event in events:
        if event['uid'] in hidden:
            continue
        result.append({
            'kind': event['kind'],
            'icon': ICONS[event['kind']],
            'text': event_text(event),
            'when': relative_time(event['at'], now),
        })
        if len(result) >= MAX_EVENTS:
            break
    return result


def show_panel(events):
    """A sparse feed looks dead, so the panel needs at least three events."""
    return len(events) >= MIN_EVENTS_TO_SHOW

