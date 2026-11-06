"""Progress for squad challenges, from admin-approved donations.

Squads are public ones only (a viewer can also ask for their own squads).
A member's donations are capped at what the donation interval allows inside
the window, so logging extra donations never speeds a squad up. Individuals
are named only if they allow it on the leaderboard; squad totals include
everyone.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, F

from rewards.leaderboard import display_name, initials
from rewards.models import Donation
from squads.models import Squad, SquadMember

User = get_user_model()


def per_member_cap(challenge):
    """Most donations one person can legitimately make in the window."""
    interval = max(int(getattr(settings, 'DONATION_INTERVAL_DAYS', 120)), 1)
    return (challenge.days - 1) // interval + 1


def _window_donations(challenge):
    return Donation.objects.filter(
        status=Donation.Status.APPROVED,
        donation_date__range=(challenge.starts_on, challenge.ends_on),
    ).order_by()


def standings(challenge, squad_ids=None):
    """Ranked squads with counted donations.

    ``squad_ids=None`` means every public squad. Squads with no counted
    donation are left out (they have not joined in yet).
    """
    cap = per_member_cap(challenge)
    donations = _window_donations(challenge)
    if squad_ids is None:
        donations = donations.filter(
            donor__squad_memberships__squad__is_public=True,
        )
    else:
        donations = donations.filter(
            donor__squad_memberships__squad__in=list(squad_ids),
        )
    pairs = donations.values(
        sq=F('donor__squad_memberships__squad'), dn=F('donor'),
    ).annotate(c=Count('id'))
    totals = {}
    for row in pairs:
        totals[row['sq']] = totals.get(row['sq'], 0) + min(row['c'], cap)

    squads = Squad.objects.filter(pk__in=totals.keys()).annotate(
        member_count=Count('memberships'),
    )
    target = challenge.target_donations
    rows = []
    for squad in squads:
        count = totals[squad.pk]
        rows.append({
            'squad': squad,
            'donations': count,
            'members': squad.member_count,
            'percent': min(100, int(count * 100 / target)),
            'reached': count >= target,
            'is_public': squad.is_public,
        })
    rows.sort(key=lambda r: (-r['donations'], -r['members'], r['squad'].name))
    rank = 0
    previous = None
    for position, row in enumerate(rows, start=1):
        if row['donations'] != previous:
            rank, previous = position, row['donations']
        row['rank'] = rank
    return rows


def top_donors(challenge, limit=5):
    """Named contributors from public squads who allow naming."""
    cap = per_member_cap(challenge)
    rows = list(
        _window_donations(challenge).filter(
            donor__in=SquadMember.objects.filter(
                squad__is_public=True,
            ).values('user_id'),
            donor__profile__show_on_leaderboard=True,
        ).values('donor').annotate(c=Count('id'))
    )
    rows = [(r['donor'], min(r['c'], cap)) for r in rows]
    rows.sort(key=lambda r: (-r[1], r[0]))
    rows = rows[:limit]
    users = User.objects.in_bulk([pk for pk, _c in rows])
    result = []
    for pk, count in rows:
        name = display_name(users[pk])
        result.append({'name': name, 'initials': initials(name), 'donations': count})
    return result


def headline(challenge, rows):
    """Numbers for the progress bar at the top of the detail page."""
    target = challenge.target_donations
    total = sum(r['donations'] for r in rows)
    reached = sum(1 for r in rows if r['reached'])
    leader = rows[0] if rows else None
    return {
        'total': total,
        'squads': len(rows),
        'reached': reached,
        'leader': leader,
        'leader_percent': leader['percent'] if leader else 0,
        'target': target,
    }
