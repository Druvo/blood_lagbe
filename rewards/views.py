from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.text import slugify

from rewards.leaderboard import (
    CANDIDATE_POOL,
    PERIOD_ALL,
    PERIOD_MONTH,
    display_name as _display_name,
    get_leaderboard,
    get_monthly_awards,
    get_request_heroes,
    get_user_standing,
    initials as _initials,
)
from rewards.geo import DISTRICT_CHOICES, DISTRICT_NAMES
from rewards.models import (
    get_current_streak,
    get_donor_badge_progress,
    get_donor_points,
    get_earned_hero_badges,
    lives_saved,
    request_donation_count,
)

User = get_user_model()


# The donor hub lives in rewards/hub_views.py; kept importable from here.
from rewards.hub_views import hub as log_donation  # noqa: E402,F401


def badge_card(request, user_id):
    """Public, shareable card showing a donor's current tier and streak."""
    donor = get_object_or_404(User.objects.select_related('profile'), id=user_id)
    viewer = request.user
    # Public only for donors who allow card sharing AND have not hidden
    # themselves from public listings: the card shows their name.
    can_view = (
        donor.profile.public_badge_card and donor.profile.show_on_leaderboard
    ) or (
        viewer.is_authenticated and (viewer.pk == donor.pk or viewer.is_staff)
    )
    if not can_view:
        raise Http404('This donor keeps their badge card private.')
    current_badge, _, approved_count, _ = get_donor_badge_progress(donor)
    current_streak = get_current_streak(donor)
    heroes = get_earned_hero_badges(request_donation_count(donor))
    hero = heroes[-1] if heroes else None
    points = get_donor_points(donor)
    lives = lives_saved(approved_count)
    name = _display_name(donor)
    tier_name = current_badge.name if current_badge else 'Aspiring Donor'
    share_url = request.build_absolute_uri(request.path)

    return render(request, 'rewards/badge_card.html', {
        'share_url': share_url,
        'share_text': (
            f"{name} is a {tier_name} on {settings.SITE_NAME}: {approved_count} "
            f"verified donation{'s' if approved_count != 1 else ''}, "
            f"an estimated {lives} people helped at most. Join us and donate blood!"
        ),
        'share_filename': f'{slugify(settings.SITE_NAME) or "donor"}-badge-{donor.pk}.png',
        'share_card': {
            'type': 'badge',
            'siteName': settings.SITE_NAME,
            'name': name,
            'tier': tier_name,
            'tierColor': current_badge.color if current_badge else '#999999',
            'hero': hero.name if hero else '',
            'heroColor': hero.color if hero else '',
            'donations': approved_count,
            'streak': current_streak,
            'points': points,
            'lives': lives,
        },
        'share_preview': True,
        'display_name': name,
        'current_badge': current_badge,
        'hero_badge': hero,
        'approved_count': approved_count,
        'current_streak': current_streak,
        'points': points,
        'lives': lives,
    })


def leaderboard(request):
    """Public leaderboard (all-time or this month, optionally one district)
    plus monthly awards and request heroes."""
    period = request.GET.get('period', PERIOD_ALL)
    if period not in (PERIOD_ALL, PERIOD_MONTH):
        period = PERIOD_ALL
    district = request.GET.get('district', '')
    if district not in DISTRICT_NAMES:
        district = ''

    ranked = get_leaderboard(period, limit=CANDIDATE_POOL, district=district)
    entries = ranked[:20]
    my_entry = None
    my_standing = None
    if request.user.is_authenticated:
        my_entry = next(
            (e for e in ranked if e['user'].id == request.user.id), None,
        )
        if my_entry is None:
            my_standing = get_user_standing(request.user, period, district)

    return render(request, 'rewards/leaderboard.html', {
        'period': period,
        'district': district,
        'district_choices': DISTRICT_CHOICES,
        'entries': entries,
        'podium': entries[:3],
        'rest': entries[3:],
        'my_entry': my_entry,
        'my_standing': my_standing,
        'my_initials': (
            _initials(_display_name(request.user))
            if request.user.is_authenticated else ''
        ),
        'pool_size': CANDIDATE_POOL,
        'hidden_from_board': (
            request.user.is_authenticated
            and not request.user.profile.show_on_leaderboard
        ),
        'monthly': get_monthly_awards(),
        'heroes': get_request_heroes(period, district),
    })
