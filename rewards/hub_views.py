"""The donor hub ("My hub"): dashboard, history, printable pages, CSV export
and deleting your own pending donations. Everything here is about the logged
in donor's own data."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from rewards.exports import donations_csv_response
from rewards.forms import DonationForm
from rewards.leaderboard import display_name
from rewards.models import (
    Badge,
    BloodRequest,
    Donation,
    get_current_streak,
    get_donor_badge_progress,
    get_donor_points,
    get_earned_hero_badges,
    get_earned_streak_badges,
    get_eligibility,
    lives_saved,
    request_donation_count,
)
from rewards.quests import get_level, get_quests

HUB_HISTORY_LIMIT = 8
RING_CIRCUMFERENCE = 276.46  # 2 * pi * r, with r = 44 in the template


def _form_defaults(user):
    """Start the form from what we already know about the donor."""
    profile = user.profile
    initial = {'donation_date': timezone.localdate()}
    if profile.blood_group:
        initial['blood_group'] = profile.blood_group
    if profile.district:
        initial['district'] = profile.district
    return initial


def _urgent_matches(user, blood_group):
    """Open requests for the donor's own group that they have not answered
    yet and did not post themselves."""
    if not blood_group:
        return 0
    return (
        BloodRequest.objects.still_needed().public()
        .filter(blood_group=blood_group)
        .exclude(requester=user)
        .exclude(responses__donor=user)
        .count()
    )


@login_required
def hub(request):
    user = request.user
    if request.method == 'POST':
        form = DonationForm(request.POST, user=user)
        if form.is_valid():
            donation = form.save(commit=False)
            donation.donor = user
            donation.save()
            messages.success(
                request,
                "Donation logged! It'll show up once an admin verifies it.",
            )
            return redirect('log-donation')
    else:
        form = DonationForm(user=user, initial=_form_defaults(user))
    form.fields['donation_date'].widget.attrs['max'] = (
        timezone.localdate().isoformat()
    )

    donations = list(Donation.objects.filter(donor=user))
    pending_count = sum(
        1 for d in donations if d.status == Donation.Status.PENDING
    )
    logged_count = sum(
        1 for d in donations if d.status != Donation.Status.REJECTED
    )

    current_badge, next_badge, approved_count, progress_percent = (
        get_donor_badge_progress(user)
    )
    remaining_to_next = (
        next_badge.min_donations - approved_count if next_badge else 0
    )
    current_streak = get_current_streak(user)
    request_donations = request_donation_count(user)
    points = get_donor_points(user)
    eligibility = get_eligibility(user)
    profile = user.profile

    quests = get_quests(
        user, approved_count=approved_count, streak=current_streak,
        request_donations=request_donations,
    )
    urgent_matches = (
        _urgent_matches(user, profile.blood_group)
        if eligibility.eligible else 0
    )

    return render(request, 'rewards/log_donation.html', {
        'display_name': display_name(user),
        'first_name': user.first_name or '',
        'profile': profile,
        'hero_badges': Badge.objects.filter(badge_type=Badge.Type.HERO),
        'earned_hero_badges': get_earned_hero_badges(request_donations),
        'request_donations': request_donations,
        'form': form,
        'donations': donations,
        'recent_donations': donations[:HUB_HISTORY_LIMIT],
        'more_donations': max(len(donations) - HUB_HISTORY_LIMIT, 0),
        'pending_count': pending_count,
        'logged_count': logged_count,
        'current_badge': current_badge,
        'next_badge': next_badge,
        'approved_count': approved_count,
        'progress_percent': progress_percent,
        'remaining_to_next': remaining_to_next,
        'all_badges': Badge.objects.filter(badge_type=Badge.Type.TIER),
        'current_streak': current_streak,
        'points': points,
        'level_info': get_level(points),
        'streak_badges': Badge.objects.filter(badge_type=Badge.Type.STREAK),
        'earned_streak_badges': get_earned_streak_badges(current_streak),
        'eligibility': eligibility,
        'ring_offset': round(
            RING_CIRCUMFERENCE * (1 - eligibility.percent / 100), 2,
        ),
        'lives': lives_saved(approved_count),
        'quests': quests,
        'open_quests': [q for q in quests if not q.done][:3],
        'quests_done': sum(1 for q in quests if q.done),
        'urgent_matches': urgent_matches,
        'has_requests_to_link': form.fields[
            'blood_request'
        ].queryset.exists(),
    })


def _verified(donations):
    return [d for d in donations if d.status == Donation.Status.APPROVED]


def _own_summary(user, donations):
    """Shared header numbers for the printable pages. No phone number."""
    verified = _verified(donations)
    donor_blood = user.profile.blood_group
    if not donor_blood and donations:
        donor_blood = donations[0].blood_group
    return {
        'display_name': display_name(user),
        'blood_group': donor_blood,
        'verified_count': len(verified),
        'total_count': len(donations),
        'pending_count': sum(
            1 for d in donations if d.status == Donation.Status.PENDING
        ),
        'last_verified': (
            max(d.donation_date for d in verified) if verified else None
        ),
        'printed_on': timezone.localdate(),
    }


@login_required
def history(request):
    """Printable donation history. `?only=verified` hides unverified rows."""
    only_verified = request.GET.get('only') == 'verified'
    donations = list(Donation.objects.filter(donor=request.user))
    summary = _own_summary(request.user, donations)
    rows = _verified(donations) if only_verified else donations
    return render(request, 'rewards/history_print.html', {
        **summary,
        'rows': rows,
        'only_verified': only_verified,
    })


@login_required
def export_csv(request):
    donations = Donation.objects.filter(donor=request.user).order_by(
        '-donation_date', '-pk',
    )
    return donations_csv_response(donations)


@login_required
def donor_card(request):
    user = request.user
    donations = list(Donation.objects.filter(donor=user))
    summary = _own_summary(user, donations)
    current_badge, _next, _count, _percent = get_donor_badge_progress(user)
    since = user.profile.created_at
    if since is None and donations:
        since = min(d.donation_date for d in donations)
    return render(request, 'rewards/donor_card.html', {
        **summary,
        'current_badge': current_badge,
        'member_since': since,
        'level_info': get_level(get_donor_points(user)),
    })


@login_required
@require_http_methods(['GET', 'POST'])
def delete_donation(request, pk):
    """Let a donor take back a donation that is still pending. Only the owner
    can, and only while it is pending: once an admin has decided, the record
    is part of the verified history."""
    donation = get_object_or_404(Donation, pk=pk, donor=request.user)
    if donation.status != Donation.Status.PENDING:
        raise PermissionDenied('Only pending donations can be deleted.')
    if request.method == 'POST':
        # Re-check the status in the delete itself so an admin approving it
        # at the same moment wins.
        deleted, _ = Donation.objects.filter(
            pk=donation.pk, donor=request.user,
            status=Donation.Status.PENDING,
        ).delete()
        if deleted:
            messages.success(request, 'Pending donation deleted.')
        else:
            messages.error(
                request, 'That donation was just reviewed and can no longer be deleted.',
            )
        return redirect('log-donation')
    return render(request, 'rewards/hub_delete_confirm.html', {
        'donation': donation,
    })
