from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from rewards.forms import DonationForm
from rewards.models import Donation


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

    return render(request, 'rewards/log_donation.html', {
        'form': form,
        'donations': donations,
    })
