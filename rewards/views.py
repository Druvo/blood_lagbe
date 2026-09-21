from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from rewards.forms import DonationForm
from rewards.models import (
    Badge,
    Donation,
    get_current_streak,
    get_donor_badge_progress,
    get_donor_points,
    get_earned_streak_badges,
)

User = get_user_model()


def _display_name(user):
    return ' '.join(filter(None, [user.first_name, user.last_name])) or user.phone


@login_required
def log_donation(request):
    if request.method == 'POST':
        form = DonationForm(request.POST)
        if form.is_valid():
            donation = form.save(commit=False)
            donation.donor = request.user
            donation.save()
            messages.success(
                request,
                "Donation logged! It'll show up once an admin verifies it.",
            )
            return redirect('log-donation')
    else:
        form = DonationForm()

    donations = Donation.objects.filter(donor=request.user)
    current_badge, next_badge, approved_count, progress_percent = (
        get_donor_badge_progress(request.user)
    )
    remaining_to_next = (
        next_badge.min_donations - approved_count if next_badge else 0
    )
    current_streak = get_current_streak(request.user)

    return render(request, 'rewards/log_donation.html', {
        'form': form,
        'donations': donations,
        'current_badge': current_badge,
        'next_badge': next_badge,
        'approved_count': approved_count,
        'progress_percent': progress_percent,
        'remaining_to_next': remaining_to_next,
        'all_badges': Badge.objects.filter(badge_type=Badge.Type.TIER),
        'current_streak': current_streak,
        'points': get_donor_points(request.user),
        'streak_badges': Badge.objects.filter(badge_type=Badge.Type.STREAK),
        'earned_streak_badges': get_earned_streak_badges(current_streak),
    })


def badge_card(request, user_id):
    """Public, shareable card showing a donor's current tier and streak."""
    donor = get_object_or_404(User, id=user_id)
    current_badge, _, approved_count, _ = get_donor_badge_progress(donor)
    current_streak = get_current_streak(donor)

    return render(request, 'rewards/badge_card.html', {
        'display_name': _display_name(donor),
        'current_badge': current_badge,
        'approved_count': approved_count,
        'current_streak': current_streak,
        'points': get_donor_points(donor),
    })
