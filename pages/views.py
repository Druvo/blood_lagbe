from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import render

from rewards.models import Badge

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
        .filter(approved_count__gt=0)
        .order_by('-approved_count')[:6]
    )

    badges_desc = list(Badge.objects.order_by('-min_donations'))

    leaderboard = []
    for donor in donors:
        display_name = ' '.join(
            filter(None, [donor.first_name, donor.last_name])
        ) or donor.phone
        latest_donation = (
            donor.donations.filter(status='approved')
            .order_by('-donation_date').first()
        )
        leaderboard.append({
            'donor': donor,
            'display_name': display_name,
            'badge': _badge_for_count(badges_desc, donor.approved_count),
            'approved_count': donor.approved_count,
            'latest_donation': latest_donation,
        })

    return render(request, "pages/home.html", {'leaderboard': leaderboard})
