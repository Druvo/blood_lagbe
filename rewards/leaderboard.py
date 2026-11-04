"""Leaderboard rankings and monthly awards, computed from verified donations."""
import calendar

from django.contrib.auth import get_user_model
from django.db.models import Count, Min, Q
from django.utils import timezone

from rewards.models import (
    REQUEST_BONUS_POINTS,
    Badge,
    Donation,
    get_current_streak,
    lives_saved,
)

User = get_user_model()

PERIOD_ALL = 'all'
PERIOD_MONTH = 'month'
# Rank on a cheap database-side score first, then add streak bonuses to this
# many candidates only, so the page stays fast as donors grow.
CANDIDATE_POOL = 50


def display_name(user):
    """Public name. Never falls back to the phone number: it is the login
    and contact detail, and these names appear on public pages."""
    return (
        ' '.join(filter(None, [user.first_name, user.last_name]))
        or f'Donor #{user.pk}'
    )


def month_bounds(today=None):
    today = today or timezone.localdate()
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    return first, last


def initials(name):
    """Up to two letters for an avatar. Works for Bangla names too."""
    parts = [p for p in name.split() if p[0].isalpha()]
    if not parts:
        return '?'
    letters = parts[0][0] + (parts[-1][0] if len(parts) > 1 else '')
    return letters.upper()


def _approved(period, public_only=False, district=''):
    # order_by() clears Donation's default ordering: left in place it joins
    # the GROUP BY of every values().annotate() below and splits one donor
    # into one row per donation date.
    approved = Donation.objects.filter(
        status=Donation.Status.APPROVED,
    ).order_by()
    if public_only:
        # Donors who opted out stay in aggregate totals, never in rankings.
        approved = approved.filter(donor__profile__show_on_leaderboard=True)
    if district:
        approved = approved.filter(district=district)
    if period == PERIOD_MONTH:
        first, last = month_bounds()
        approved = approved.filter(donation_date__range=(first, last))
    return approved


def _tier_for(badges_desc, count):
    for badge in badges_desc:
        if count >= badge.min_donations:
            return badge
    return None


def get_leaderboard(period=PERIOD_ALL, limit=20, district=''):
    """Top donors, ranked by points earned in the period.

    Points = 10 per verified donation + 20 bonus for each one made to an
    urgent request. All-time points also include the 5-per-cycle streak
    bonus, matching the donor's own points total. A district filter ranks
    only the donations made in that district, so it has no streak bonus.
    Donors with the same points and donations share a rank.
    """
    rows = list(
        _approved(period, public_only=True, district=district)
        .values('donor').annotate(
            count=Count('id'),
            requests=Count('id', filter=Q(blood_request__isnull=False)),
        )
    )
    for row in rows:
        row['points'] = row['count'] * 10 + row['requests'] * REQUEST_BONUS_POINTS
    rows.sort(key=lambda r: (-r['points'], -r['count'], r['donor']))
    rows = rows[:CANDIDATE_POOL]

    users = User.objects.select_related('profile').in_bulk([r['donor'] for r in rows])
    if period == PERIOD_ALL and not district:
        for row in rows:
            row['points'] += get_current_streak(users[row['donor']]) * 5
        rows.sort(key=lambda r: (-r['points'], -r['count'], r['donor']))

    lifetime = {
        r['donor']: r['c'] for r in
        Donation.objects.filter(
            donor__in=users.keys(), status=Donation.Status.APPROVED,
        ).order_by().values('donor').annotate(c=Count('id'))
    }
    badges_desc = list(
        Badge.objects.filter(badge_type=Badge.Type.TIER).order_by('-min_donations')
    )

    entries = []
    previous = None
    rank = 0
    for position, row in enumerate(rows[:limit], start=1):
        user = users[row['donor']]
        total = lifetime.get(row['donor'], 0)
        key = (row['points'], row['count'])
        if key != previous:
            rank = position
            previous = key
        entries.append({
            'rank': rank,
            'user': user,
            'name': display_name(user),
            'initials': initials(display_name(user)),
            'district': user.profile.district,
            'points': row['points'],
            'donations': row['count'],
            'request_donations': row['requests'],
            'lives': lives_saved(total),
            'badge': _tier_for(badges_desc, total),
            'card_public': user.profile.public_badge_card,
        })
    rank_counts = {}
    for entry in entries:
        rank_counts[entry['rank']] = rank_counts.get(entry['rank'], 0) + 1
    for i, entry in enumerate(entries):
        entry['tied'] = rank_counts[entry['rank']] > 1
        # Points still needed to pass the nearest donor ranked above.
        above = next(
            (e for e in reversed(entries[:i]) if e['rank'] < entry['rank']),
            None,
        )
        entry['gap'] = above['points'] - entry['points'] if above else 0
    return entries


def get_request_heroes(period=PERIOD_ALL, district='', limit=3):
    """Public donors with the most verified donations to urgent requests."""
    rows = list(
        _approved(period, public_only=True, district=district)
        .filter(blood_request__isnull=False)
        .values('donor').annotate(count=Count('id'))
        .order_by('-count', 'donor')[:limit]
    )
    users = User.objects.select_related('profile').in_bulk(
        [r['donor'] for r in rows],
    )
    return [{
        'user': users[r['donor']],
        'name': display_name(users[r['donor']]),
        'initials': initials(display_name(users[r['donor']])),
        'count': r['count'],
        'card_public': users[r['donor']].profile.public_badge_card,
    } for r in rows]


def get_user_standing(user, period=PERIOD_ALL, district=''):
    """The user's own points and donations in the period, even when they
    are outside the ranked pool. (0, 0) if they have none."""
    row = _approved(period, district=district).filter(donor=user).aggregate(
        count=Count('id'),
        requests=Count('id', filter=Q(blood_request__isnull=False)),
    )
    points = row['count'] * 10 + row['requests'] * REQUEST_BONUS_POINTS
    if period == PERIOD_ALL and not district and row['count']:
        points += get_current_streak(user) * 5
    return {'points': points, 'donations': row['count']}


def _top(queryset, **annotation):
    """Highest donor by `count` (then lowest id, so ties are stable)."""
    row = (
        queryset.values('donor').annotate(count=Count('id'), **annotation)
        .order_by('-count', 'donor').first()
    )
    if not row:
        return None, 0
    user = User.objects.select_related('profile').get(pk=row['donor'])
    return user, row['count']


def get_monthly_awards():
    """Awards for the current calendar month, plus month-so-far totals."""
    first, last = month_bounds()
    month = _approved(PERIOD_MONTH)
    public_month = _approved(PERIOD_MONTH, public_only=True)

    awards = []
    top_user, top_count = _top(public_month)
    if top_user:
        awards.append({
            'title': 'Donor of the Month', 'icon': 'award',
            'color': '#d4af37', 'user': top_user,
            'name': display_name(top_user),
            'card_public': top_user.profile.public_badge_card,
            'detail': f'{top_count} verified donation'
                      f'{"s" if top_count != 1 else ""} this month',
        })

    hero_user, hero_count = _top(public_month.filter(blood_request__isnull=False))
    if hero_user:
        awards.append({
            'title': 'Lifeline Hero', 'icon': 'heart',
            'color': '#e50202', 'user': hero_user,
            'name': display_name(hero_user),
            'card_public': hero_user.profile.public_badge_card,
            'detail': f'{hero_count} urgent request'
                      f'{"s" if hero_count != 1 else ""} answered this month',
        })

    month_count = month.count()
    new_donors = (
        Donation.objects.filter(status=Donation.Status.APPROVED).order_by()
        .values('donor').annotate(first=Min('donation_date'))
        .filter(first__range=(first, last)).count()
    )
    return {
        'month_label': first.strftime('%B %Y'),
        'awards': awards,
        'donations': month_count,
        'lives': lives_saved(month_count),
        'new_donors': new_donors,
    }
