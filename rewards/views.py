from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from rewards.forms import BloodRequestForm, DonationForm
from rewards.leaderboard import (
    CANDIDATE_POOL,
    PERIOD_ALL,
    PERIOD_MONTH,
    display_name as _display_name,
    get_leaderboard,
    get_monthly_awards,
)
from rewards.models import (
    BLOOD_GROUP_CHOICES,
    Badge,
    BloodRequest,
    Donation,
    RequestResponse,
    get_current_streak,
    get_donor_badge_progress,
    get_donor_points,
    get_earned_hero_badges,
    get_earned_streak_badges,
    get_eligibility,
    lives_saved,
    request_donation_count,
)

User = get_user_model()


@login_required
def log_donation(request):
    if request.method == 'POST':
        form = DonationForm(request.POST, user=request.user)
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
        form = DonationForm(user=request.user)

    donations = Donation.objects.filter(donor=request.user)
    current_badge, next_badge, approved_count, progress_percent = (
        get_donor_badge_progress(request.user)
    )
    remaining_to_next = (
        next_badge.min_donations - approved_count if next_badge else 0
    )
    current_streak = get_current_streak(request.user)
    request_donations = request_donation_count(request.user)

    return render(request, 'rewards/log_donation.html', {
        'hero_badges': Badge.objects.filter(badge_type=Badge.Type.HERO),
        'earned_hero_badges': get_earned_hero_badges(request_donations),
        'request_donations': request_donations,
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
        'eligibility': get_eligibility(request.user),
        'lives': lives_saved(approved_count),
    })


def badge_card(request, user_id):
    """Public, shareable card showing a donor's current tier and streak."""
    donor = get_object_or_404(User, id=user_id)
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
            f"{name} is a {tier_name} on Blood Lagbe: {approved_count} "
            f"verified donation{'s' if approved_count != 1 else ''}, "
            f"an estimated {lives} people helped at most. Join us and donate blood!"
        ),
        'share_filename': f'blood-lagbe-{donor.pk}.png',
        'share_card': {
            'type': 'badge',
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
        'display_name': name,
        'current_badge': current_badge,
        'hero_badge': hero,
        'approved_count': approved_count,
        'current_streak': current_streak,
        'points': points,
        'lives': lives,
    })


def leaderboard(request):
    """Public leaderboard (all-time or this month) plus monthly awards."""
    period = request.GET.get('period', PERIOD_ALL)
    if period not in (PERIOD_ALL, PERIOD_MONTH):
        period = PERIOD_ALL

    ranked = get_leaderboard(period, limit=CANDIDATE_POOL)
    my_entry = None
    if request.user.is_authenticated:
        my_entry = next(
            (e for e in ranked if e['user'].id == request.user.id), None,
        )

    return render(request, 'rewards/leaderboard.html', {
        'period': period,
        'entries': ranked[:20],
        'my_entry': my_entry,
        'monthly': get_monthly_awards(),
    })


def request_list(request):
    """Public board of requests that still need donors."""
    selected = request.GET.get('blood_group', '')
    requests_qs = BloodRequest.objects.still_needed().select_related('requester')
    if selected in dict(BLOOD_GROUP_CHOICES):
        requests_qs = requests_qs.filter(blood_group=selected)
    else:
        selected = ''

    return render(request, 'rewards/request_list.html', {
        'blood_requests': requests_qs,
        'blood_groups': [code for code, _ in BLOOD_GROUP_CHOICES],
        'selected': selected,
        'eligibility': (
            get_eligibility(request.user)
            if request.user.is_authenticated else None
        ),
    })


def request_detail(request, pk):
    blood_request = get_object_or_404(
        BloodRequest.objects.select_related('requester'), pk=pk,
    )
    user = request.user
    is_owner = user.is_authenticated and (
        user == blood_request.requester or user.is_staff
    )
    responses = []
    if is_owner:
        responses = [
            {'name': _display_name(r.donor), 'phone': r.donor.phone}
            for r in blood_request.responses.select_related('donor')
        ]

    share_url = request.build_absolute_uri(request.path)
    return render(request, 'rewards/request_detail.html', {
        'share_url': share_url,
        'share_text': (
            f'Urgent: {blood_request.blood_group} blood needed at '
            f'{blood_request.hospital}, {blood_request.district}. '
            'Can you help?'
        ),
        'share_filename': f'blood-request-{blood_request.pk}.png',
        'share_card': {
            'type': 'request',
            'bloodGroup': blood_request.blood_group,
            'hospital': blood_request.hospital,
            'district': blood_request.district,
            'remaining': blood_request.units_remaining,
            'neededBy': f'{blood_request.needed_by:%b %d}',
        },
        'blood_request': blood_request,
        'is_owner': is_owner,
        'responses': responses,
        'response_count': blood_request.responses.count(),
        'has_responded': (
            user.is_authenticated
            and blood_request.responses.filter(donor=user).exists()
        ),
        'eligibility': (
            get_eligibility(user) if user.is_authenticated else None
        ),
    })


@login_required
def request_create(request):
    if request.method == 'POST':
        form = BloodRequestForm(request.POST, user=request.user)
        if form.is_valid():
            blood_request = form.save(commit=False)
            blood_request.requester = request.user
            blood_request.save()
            messages.success(request, 'Request posted. Donors can now see it.')
            return redirect('request-detail', pk=blood_request.pk)
    else:
        form = BloodRequestForm(user=request.user)

    return render(request, 'rewards/request_form.html', {'form': form})


@login_required
@require_POST
def request_respond(request, pk):
    blood_request = get_object_or_404(BloodRequest, pk=pk)

    if blood_request.requester_id == request.user.id:
        messages.error(request, "You can't respond to your own request.")
    elif not blood_request.is_active:
        messages.error(request, 'This request is no longer open.')
    else:
        eligibility = get_eligibility(request.user)
        if not eligibility.eligible:
            messages.error(
                request,
                f'You can donate again on {eligibility.next_date:%b %d, %Y}. '
                'Please let someone else respond to this request.',
            )
        else:
            RequestResponse.objects.get_or_create(
                blood_request=blood_request, donor=request.user,
            )
            messages.success(
                request,
                'Thank you! The requester can now see your name and phone.',
            )
    return redirect('request-detail', pk=pk)


@login_required
@require_POST
def request_close(request, pk):
    blood_request = get_object_or_404(BloodRequest, pk=pk)
    if request.user == blood_request.requester or request.user.is_staff:
        blood_request.status = BloodRequest.Status.CLOSED
        blood_request.save(update_fields=['status'])
        messages.success(request, 'Request closed.')
    else:
        messages.error(request, "You can't close someone else's request.")
    return redirect('request-detail', pk=pk)
