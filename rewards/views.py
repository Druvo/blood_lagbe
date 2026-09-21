from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from rewards.forms import DonationForm
from rewards.models import Badge, Donation, get_donor_badge_progress


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

    return render(request, 'rewards/log_donation.html', {
        'form': form,
        'donations': donations,
        'current_badge': current_badge,
        'next_badge': next_badge,
        'approved_count': approved_count,
        'progress_percent': progress_percent,
        'remaining_to_next': remaining_to_next,
        'all_badges': Badge.objects.all(),
    })
