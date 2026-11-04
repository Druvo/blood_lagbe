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
    return ' '.join(filter(None, [user.first_name, user.last_name])) or user.phone


def month_bounds(today=None):
    today = today or timezone.localdate()
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    return first, last


def _approved(period):
    # order_by() clears Donation's default ordering: left in place it joins
    # the GROUP BY of every values().annotate() below and splits one donor
    # into one row per donation date.
    approved = Donation.objects.filter(
        status=Donation.Status.APPROVED,
    ).order_by()
    if period == PERIOD_MONTH:
        first, last = month_bounds()
        approved = approved.filter(donation_date__range=(first, last))
    return approved


def _tier_for(badges_desc, count):
    for badge in badges_desc:
        if count >= badge.min_donations:
            return badge
    return None


def get_leaderboard(period=PERIOD_ALL, limit=20):
    """Top donors, ranked by points earned in the period.

    Points = 10 per verified donation + 20 bonus for each one made to an
    urgent request. All-time points also include the 5-per-cycle streak
    bonus, matching the donor's own points total.
    """
    rows = list(
        _approved(period).values('donor').annotate(
            count=Count('id'),
            requests=Count('id', filter=Q(blood_request__isnull=False)),
        )
    )
    for row in rows:
        row['points'] = row['count'] * 10 + row['requests'] * REQUEST_BONUS_POINTS
    rows.sort(key=lambda r: (-r['points'], -r['count'], r['donor']))
    rows = rows[:CANDIDATE_POOL]

    users = User.objects.in_bulk([r['donor'] for r in rows])
    streaks = {}
    if period == PERIOD_ALL:
        for row in rows:
            streaks[row['donor']] = get_current_streak(users[row['donor']])
            row['points'] += streaks[row['donor']] * 5
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
    for rank, row in enumerate(rows[:limit], start=1):
        user = users[row['donor']]
        total = lifetime.get(row['donor'], 0)
        entries.append({
            'rank': rank,
            'user': user,
            'name': display_name(user),
            'points': row['points'],
            'donations': row['count'],
            'request_donations': row['requests'],
            'lives': lives_saved(total),
            'badge': _tier_for(badges_desc, total),
        })
    return entries


def _top(queryset, **annotation):
    """Highest donor by `count` (then lowest id, so ties are stable)."""
    row = (
        queryset.values('donor').annotate(count=Count('id'), **annotation)
        .order_by('-count', 'donor').first()
    )
    if not row:
        return None, 0
    return User.objects.get(pk=row['donor']), row['count']


def get_monthly_awards():
    """Awards for the current calendar month, plus month-so-far totals."""
    first, last = month_bounds()
    month = _approved(PERIOD_MONTH)

    awards = []
    top_user, top_count = _top(month)
    if top_user:
        awards.append({
            'title': 'Donor of the Month', 'icon': 'award',
            'color': '#d4af37', 'user': top_user,
            'name': display_name(top_user),
            'detail': f'{top_count} verified donation'
                      f'{"s" if top_count != 1 else ""} this month',
        })

    hero_user, hero_count = _top(month.filter(blood_request__isnull=False))
    if hero_user:
        awards.append({
            'title': 'Lifeline Hero', 'icon': 'heart',
            'color': '#e50202', 'user': hero_user,
            'name': display_name(hero_user),
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
