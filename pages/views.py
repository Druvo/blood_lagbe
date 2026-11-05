from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import render

from rewards.leaderboard import display_name
from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.models import Badge, BloodRequest, get_community_stats

User = get_user_model()


def _badge_for_count(badges_desc, count):
    for badge in badges_desc:
        if count >= badge.min_donations:
            return badge
    return None


def index(request):
    donors = (
        User.objects
        .annotate(approved_count=Count(
            'donations', filter=Q(donations__status='approved'),
        ))
        .filter(approved_count__gt=0, profile__show_on_leaderboard=True)
        .select_related('profile')
        .order_by('-approved_count', 'pk')[:6]
    )

    badges_desc = list(
        Badge.objects.filter(badge_type=Badge.Type.TIER).order_by('-min_donations')
    )

    leaderboard = []
    for donor in donors:
        latest_donation = (
            donor.donations.filter(status='approved')
            .order_by('-donation_date').first()
        )
        leaderboard.append({
            'donor': donor,
            'display_name': display_name(donor),
            'badge': _badge_for_count(badges_desc, donor.approved_count),
            'approved_count': donor.approved_count,
            'latest_donation': latest_donation,
        })

    return render(request, "pages/home.html", {
        'leaderboard': leaderboard,
        'community': get_community_stats(),
        'blood_groups': [code for code, _ in BLOOD_GROUP_CHOICES],
        'tier_badges': badges_desc[::-1],
        'urgent_requests': list(
            BloodRequest.objects.still_needed().public()
            .select_related('requester')[:3]
        ),
    })


# Content pages live in content_views.py (learn hub, FAQ, about) and seo.py
# (robots, manifest, service worker, offline page).  Re-exported here.
def csrf_failure(request, reason=''):
    """A friendly page for an expired or cross-site form post (Django's own
    CSRF 403 page is unstyled and does not say what to do)."""
    return render(request, '403_csrf.html', status=403)


from .content_views import about, faq, learn  # noqa: E402,F401
