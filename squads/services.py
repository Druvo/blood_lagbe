"""Squad rules (caps, joining, leaving) and the numbers shown on squad pages.

Totals count admin-approved donations only. Members who hid themselves from
the leaderboard stay inside the totals but never appear by name.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, F, Q

from rewards.leaderboard import (
    PERIOD_ALL,
    PERIOD_MONTH,
    display_name,
    initials,
    month_bounds,
)
from rewards.models import REQUEST_BONUS_POINTS, Donation, lives_saved
from squads.models import (
    MAX_MEMBERS_PER_SQUAD,
    MAX_SQUADS_PER_USER,
    Squad,
    SquadMember,
)

User = get_user_model()

JOIN_FAIL_LIMIT = 10
JOIN_FAIL_WINDOW = 15 * 60  # seconds


class SquadError(Exception):
    """A rule was broken; the message is safe to show to the user."""


def normalize_code(raw):
    return ''.join(ch for ch in (raw or '') if ch.isalnum()).upper()


def _fail_key(user):
    return f'squad-join-fail:{user.pk}'


def join_attempts_blocked(user):
    return cache.get(_fail_key(user), 0) >= JOIN_FAIL_LIMIT


def record_failed_join(user):
    key = _fail_key(user)
    cache.add(key, 0, JOIN_FAIL_WINDOW)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, JOIN_FAIL_WINDOW)


def find_by_code(raw):
    code = normalize_code(raw)
    if not code:
        return None
    return Squad.objects.filter(join_code=code).first()


def create_squad(owner, **fields):
    with transaction.atomic():
        if owner.squad_memberships.count() >= MAX_SQUADS_PER_USER:
            raise SquadError(
                f'You can be in up to {MAX_SQUADS_PER_USER} squads. '
                'Leave one to start another.'
            )
        squad = Squad.objects.create(owner=owner, **fields)
        SquadMember.objects.create(
            squad=squad, user=owner, role=SquadMember.Role.OWNER,
        )
    return squad


def join_squad(user, squad):
    """Add `user`; raises SquadError with a friendly reason if not allowed."""
    with transaction.atomic():
        if SquadMember.objects.filter(squad=squad, user=user).exists():
            raise SquadError('You are already in this squad.')
        if user.squad_memberships.count() >= MAX_SQUADS_PER_USER:
            raise SquadError(
                f'You can be in up to {MAX_SQUADS_PER_USER} squads. '
                'Leave one to join another.'
            )
        if squad.memberships.count() >= MAX_MEMBERS_PER_SQUAD:
            raise SquadError('This squad is full.')
        return SquadMember.objects.create(squad=squad, user=user)


def _points(count, requests):
    return count * 10 + requests * REQUEST_BONUS_POINTS


def _member_donations(squad, period):
    qs = Donation.objects.filter(
        status=Donation.Status.APPROVED,
        donor__in=squad.memberships.values('user_id'),
    ).order_by()
    if period == PERIOD_MONTH:
        qs = qs.filter(donation_date__range=month_bounds())
    return qs


def squad_totals(squad):
    """Headline numbers. Opted-out members' donations are included, since
    these are aggregates."""
    month = _member_donations(squad, PERIOD_MONTH).count()
    total = _member_donations(squad, PERIOD_ALL).count()
    return {
        'members': squad.memberships.count(),
        'month': month,
        'all': total,
        'lives': lives_saved(total),
    }


def member_board(squad, period=PERIOD_ALL):
    """Ranked members who allow it. Others are never named."""
    rows = {
        r['donor']: r for r in _member_donations(squad, period)
        .values('donor').annotate(
            count=Count('id'),
            requests=Count('id', filter=Q(blood_request__isnull=False)),
        )
    }
    members = list(
        squad.memberships.select_related('user__profile')
        .filter(user__profile__show_on_leaderboard=True)
    )
    entries = []
    for membership in members:
        row = rows.get(membership.user_id, {'count': 0, 'requests': 0})
        name = display_name(membership.user)
        entries.append({
            'user': membership.user,
            'name': name,
            'initials': initials(name),
            'role': membership.role,
            'donations': row['count'],
            'points': _points(row['count'], row['requests']),
            'card_public': membership.user.profile.public_badge_card,
        })
    entries.sort(key=lambda e: (-e['points'], -e['donations'], e['name']))
    rank = 0
    previous = None
    for position, entry in enumerate(entries, start=1):
        key = (entry['points'], entry['donations'])
        if key != previous:
            rank, previous = position, key
        entry['rank'] = rank
    return entries


def squad_ranking(period=PERIOD_ALL, limit=50):
    """Public squads ranked by verified donations by their members."""
    donations = Donation.objects.filter(
        status=Donation.Status.APPROVED,
        donor__squad_memberships__squad__is_public=True,
    ).order_by()
    if period == PERIOD_MONTH:
        donations = donations.filter(donation_date__range=month_bounds())
    counts = {
        r['squad']: r['c'] for r in donations
        .values(squad=F('donor__squad_memberships__squad'))
        .annotate(c=Count('id'))
    }
    squads = Squad.objects.filter(pk__in=counts.keys()).annotate(
        member_count=Count('memberships'),
    )
    rows = [{
        'squad': s, 'donations': counts[s.pk], 'members': s.member_count,
        'lives': lives_saved(counts[s.pk]),
    } for s in squads]
    rows.sort(key=lambda r: (-r['donations'], -r['members'], r['squad'].name))
    rows = rows[:limit]
    rank = 0
    previous = None
    for position, row in enumerate(rows, start=1):
        if row['donations'] != previous:
            rank, previous = position, row['donations']
        row['rank'] = rank
    return rows

