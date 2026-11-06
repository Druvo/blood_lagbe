"""Hero of the Month: who earned each award in a calendar month.

Three awards, one winner each:

* Donor of the Month: most APPROVED donations dated in the month.
* Squad of the Month: public squad whose members made the most approved
  donations in the month.
* Helper of the Month: most pledges ("I can help") on requests that were
  closed as fulfilled during the month.

Only members with ``Profile.show_on_leaderboard`` are ever counted, ranked or
named; everyone else is skipped entirely. Ties go to whoever's first
qualifying action in the month came earlier, then to the lowest id. The same
rule is printed on the page (``TIE_RULE``).

Public helpers for other lanes: ``heroes_for_month(year, month)``,
``current_heroes()``, ``hall_of_fame()``.
"""
import datetime

from django.core.cache import cache
from django.db.models import Count, F, Min
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import Profile
from rewards.leaderboard import display_name
from rewards.models import BloodRequest, Donation, RequestResponse
from spotlight.models import MonthlyAward
from squads.models import Squad, SquadMember

DONOR = MonthlyAward.Kind.DONOR.value
SQUAD = MonthlyAward.Kind.SQUAD.value
HELPER = MonthlyAward.Kind.HELPER.value
KINDS = (DONOR, SQUAD, HELPER)
MIN_YEAR = 2020
# Last month's awards are written a few days into the new month, so a
# donation approved in the first days of it still counts.
GRACE_DAYS = 3
HOME_CACHE_SECONDS = 120

TIE_RULE = _(
    'Ties go to whoever made their first qualifying action of the month '
    'earlier, then to the lower member number. Only members who allow it on '
    'the leaderboard are ever named or ranked.'
)

KIND_INFO = {
    DONOR: {
        'title': _('Donor of the Month'),
        'rule': _('Most verified donations in the month.'),
    },
    SQUAD: {
        'title': _('Squad of the Month'),
        'rule': _('A public squad whose members made the most verified '
                  'donations in the month.'),
    },
    HELPER: {
        'title': _('Helper of the Month'),
        'rule': _('Most pledges on requests that ended as fulfilled in the '
                  'month.'),
    },
}


# ------------------------------------------------------------------ months

def month_bounds(year, month):
    """(first day, last day) of a calendar month."""
    first = datetime.date(year, month, 1)
    nxt = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return first, nxt - datetime.timedelta(days=1)


def valid_month(year, month, today=None):
    """True for a real month from MIN_YEAR up to and including this one."""
    today = today or timezone.localdate()
    if not (MIN_YEAR <= year <= today.year and 1 <= month <= 12):
        return False
    return (year, month) <= (today.year, today.month)


def previous_month(year, month):
    return (year, month - 1) if month > 1 else (year - 1, 12)


def next_month(year, month):
    return (year, month + 1) if month < 12 else (year + 1, 1)


def _aware_range(first, last):
    start = timezone.make_aware(datetime.datetime.combine(first, datetime.time.min))
    end = timezone.make_aware(datetime.datetime.combine(
        last + datetime.timedelta(days=1), datetime.time.min))
    return start, end


# --------------------------------------------------------------- computing

def _member_info(user_id):
    """Name, district and card flag for a rankable member (or None if they
    have opted out or do not exist)."""
    profile = Profile.objects.select_related('user').filter(
        user_id=user_id, show_on_leaderboard=True,
    ).first()
    if profile is None:
        return None
    return {
        'name': display_name(profile.user),
        # The district is shown only for members who are in the directory.
        'district': profile.district if profile.in_directory else '',
        'card_public': profile.public_badge_card,
    }


def _top_donor(first, last):
    row = (
        Donation.objects.filter(
            status=Donation.Status.APPROVED,
            donation_date__range=(first, last),
            donor__profile__show_on_leaderboard=True,
        ).order_by().values('donor').annotate(
            count=Count('id'),
            first_date=Min('donation_date'),
            first_made=Min('created_at'),
        ).order_by('-count', 'first_date', 'first_made', 'donor').first()
    )
    return (row['donor'], row['count']) if row else (None, 0)


def _top_squad(first, last):
    row = (
        SquadMember.objects.filter(
            squad__is_public=True,
            user__profile__show_on_leaderboard=True,
            user__donations__status=Donation.Status.APPROVED,
            user__donations__donation_date__range=(first, last),
        ).order_by().values('squad').annotate(
            count=Count('user__donations__id'),
            first_date=Min('user__donations__donation_date'),
            first_made=Min('user__donations__created_at'),
        ).order_by('-count', 'first_date', 'first_made', 'squad').first()
    )
    return (row['squad'], row['count']) if row else (None, 0)


def _top_helper(first, last):
    start, end = _aware_range(first, last)
    row = (
        RequestResponse.objects.filter(
            donor__profile__show_on_leaderboard=True,
            blood_request__status=BloodRequest.Status.CLOSED,
            blood_request__outcome=BloodRequest.Outcome.FULFILLED_HERE,
            blood_request__closed_at__gte=start,
            blood_request__closed_at__lt=end,
        ).exclude(donor=F('blood_request__requester'))
        .order_by().values('donor').annotate(
            count=Count('id'), first_made=Min('created_at'),
        ).order_by('-count', 'first_made', 'donor').first()
    )
    return (row['donor'], row['count']) if row else (None, 0)


def _entry(kind, year, month, pk, count, source):
    """A public-safe dict for one winner, or None if they may not be named."""
    if kind == SQUAD:
        squad = Squad.objects.filter(pk=pk, is_public=True).first()
        if squad is None:
            return None
        info = {'name': squad.name, 'district': squad.district,
                'card_public': False}
    else:
        info = _member_info(pk)
        if info is None:
            return None
    return {
        'kind': kind, 'year': year, 'month': month, 'id': pk,
        'count': count, 'source': source, **info,
    }


def compute_winner(kind, year, month):
    """The live winner of one award for a month, as an entry dict or None."""
    first, last = month_bounds(year, month)
    pk, count = {
        DONOR: _top_donor, SQUAD: _top_squad, HELPER: _top_helper,
    }[kind](first, last)
    if pk is None:
        return None
    return _entry(kind, year, month, pk, count, 'live')


def heroes_for_month(year, month):
    """Winners to show for a month, in award order. A saved award wins over a
    live computation; a saved winner who has since opted out is left out."""
    saved = {
        a.kind: a for a in MonthlyAward.objects.filter(year=year, month=month)
    }
    entries = []
    for kind in KINDS:
        award = saved.get(kind)
        if award is not None:
            pk = award.squad_id if kind == SQUAD else award.user_id
            entry = _entry(kind, year, month, pk, award.count, 'saved')
        else:
            entry = compute_winner(kind, year, month)
        if entry:
            entries.append(entry)
    return entries


def current_heroes():
    """This month so far, cached briefly. The cache holds only ids, counts
    and names; opt-outs are re-checked on every call, so someone who just
    hid themselves is never shown from the cache."""
    today = timezone.localdate()
    key = f'spotlight:heroes:{today.year}-{today.month}'
    entries = cache.get(key)
    if entries is None:
        entries = heroes_for_month(today.year, today.month)
        cache.set(key, entries, HOME_CACHE_SECONDS)
    return _still_visible(entries)


def _still_visible(entries):
    member_ids = [e['id'] for e in entries if e['kind'] != SQUAD]
    hidden = set(Profile.objects.filter(
        user_id__in=member_ids, show_on_leaderboard=False,
    ).values_list('user_id', flat=True))
    return [e for e in entries
            if e['kind'] == SQUAD or e['id'] not in hidden]


def hall_of_fame(limit=36):
    """Saved awards, newest first, leaving out anyone who may not be named."""
    entries = []
    awards = MonthlyAward.objects.all()[:limit * 2]
    for award in awards:
        pk = award.squad_id if award.kind == SQUAD else award.user_id
        entry = _entry(award.kind, award.year, award.month, pk,
                       award.count, 'saved')
        if entry:
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def last_award_for(user):
    """The member's most recent saved award (their own view, any privacy)."""
    return MonthlyAward.objects.filter(user=user).first()


# ------------------------------------------------------------------ saving

def target_month(today=None):
    """The month the scheduled run should finalise: last month, once the
    grace days have passed, otherwise the month before that."""
    today = today or timezone.localdate()
    year, month = previous_month(today.year, today.month)
    if today.day <= GRACE_DAYS:
        year, month = previous_month(year, month)
    return year, month


def award_month(year, month, dry_run=False):
    """Write the final awards for a finished month. Safe to repeat: an award
    that already exists is left as it is. Returns a list of
    (entry, created) pairs."""
    results = []
    for kind in KINDS:
        if MonthlyAward.objects.filter(kind=kind, year=year, month=month).exists():
            continue
        entry = compute_winner(kind, year, month)
        if entry is None:
            continue
        if dry_run:
            results.append((entry, False))
            continue
        fields = {'squad_id': entry['id']} if kind == SQUAD else {'user_id': entry['id']}
        _, created = MonthlyAward.objects.get_or_create(
            kind=kind, year=year, month=month,
            defaults={'count': entry['count'], **fields},
        )
        results.append((entry, created))
    return results


def month_is_final(year, month, today=None):
    today = today or timezone.localdate()
    return (year, month) < (today.year, today.month)

