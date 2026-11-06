"""Year in review: one member's approved donations for a calendar year.

Only admin-approved donations count. The rank percentile compares against
donors who allow it on the leaderboard and never reveals anyone else.
"""
import calendar
import math
from collections import Counter

from django.db.models import Count

from habits.models import ShareAction
from habits.services import longest_streak, visit_days
from rewards.models import (
    DONATION_CYCLE_DAYS,
    STREAK_GRACE_DAYS,
    Badge,
    Donation,
    lives_saved,
)

# A percentile over a handful of donors says little: show it from here up.
MIN_PERCENTILE_POOL = 5
FIRST_YEAR = 2000


def approved_in_year(user, year):
    return Donation.objects.filter(
        donor=user, status=Donation.Status.APPROVED,
        donation_date__year=year,
    ).order_by('donation_date', 'pk')


def donation_years(user):
    """Years in which the member has at least one approved donation."""
    return sorted(
        {
            d.year for d in Donation.objects.filter(
                donor=user, status=Donation.Status.APPROVED,
            ).order_by().values_list('donation_date', flat=True)
        },
        reverse=True,
    )


def favorite_place(donations):
    """(name, count) of the place they donated at most, or None. Ties go to
    the most recent one. Names are compared ignoring case and spacing."""
    counts = Counter()
    latest = {}
    label = {}
    for donation in donations:
        key = ' '.join(donation.location.lower().split())
        if not key:
            continue
        counts[key] += 1
        latest[key] = donation.donation_date
        label.setdefault(key, ' '.join(donation.location.split()))
    if not counts:
        return None
    best = max(counts, key=lambda k: (counts[k], latest[k]))
    return label[best], counts[best]


def rank_percentile(user, year, mine):
    """{'top_percent', 'pool'} for donations in `year`, or None when the
    pool of opted-in donors is too small to mean anything."""
    rows = list(
        Donation.objects.filter(
            status=Donation.Status.APPROVED, donation_date__year=year,
            donor__profile__show_on_leaderboard=True,
        ).exclude(donor=user).order_by().values('donor')
        .annotate(c=Count('id'))
    )
    pool = len(rows) + 1  # everyone ranked, plus the member themselves
    if mine < 1 or pool < MIN_PERCENTILE_POOL:
        return None
    ahead = sum(1 for row in rows if row['c'] > mine)
    rank = ahead + 1
    return {'top_percent': max(1, math.ceil(rank / pool * 100)), 'pool': pool}


def badges_earned_in(user, year):
    """Badges whose threshold the member crossed during `year`, with the date.
    Worked out from donation dates, so it is the same rule the badges use."""
    approved = list(
        Donation.objects.filter(donor=user, status=Donation.Status.APPROVED)
        .order_by('donation_date', 'pk')
    )
    dates = [d.donation_date for d in approved]
    request_dates = [d.donation_date for d in approved if d.blood_request_id]

    # Running donation streak after each donation, same rule as
    # rewards.models.get_current_streak.
    gap = DONATION_CYCLE_DAYS + STREAK_GRACE_DAYS
    streak_dates = []  # (streak after this donation, its date)
    streak = 0
    for i, date in enumerate(dates):
        if i and (date - dates[i - 1]).days <= gap:
            streak += 1
        else:
            streak = 1
        streak_dates.append((streak, date))

    earned = []
    for badge in Badge.objects.all():
        when = None
        if badge.badge_type == Badge.Type.TIER and badge.min_donations:
            if len(dates) >= badge.min_donations:
                when = dates[badge.min_donations - 1]
        elif badge.badge_type == Badge.Type.STREAK and badge.min_streak:
            when = next(
                (d for s, d in streak_dates if s >= badge.min_streak), None,
            )
        elif badge.badge_type == Badge.Type.HERO and badge.min_request_donations:
            if len(request_dates) >= badge.min_request_donations:
                when = request_dates[badge.min_request_donations - 1]
        if when is not None and when.year == year:
            earned.append({'badge': badge, 'date': when})
    earned.sort(key=lambda e: (e['date'], e['badge'].name))
    return earned


def review(user, year):
    """Everything the year page and the share card show."""
    donations = list(approved_in_year(user, year))
    count = len(donations)
    months = sorted({d.donation_date.month for d in donations})
    days = visit_days(user, year)
    data = {
        'year': year,
        'count': count,
        'has_donations': count > 0,
        'lives': lives_saved(count),
        'months': [calendar.month_abbr[m] for m in months],
        'months_active': len(months),
        'place': favorite_place(donations),
        'longest_streak': longest_streak(days),
        'days_visited': len(days),
        'requests_shared': (
            ShareAction.objects.filter(user=user, day__year=year)
            .order_by().values('blood_request').distinct().count()
        ),
        'percentile': rank_percentile(user, year, count),
        'badges': badges_earned_in(user, year),
        'first_date': donations[0].donation_date if donations else None,
    }
    return data
