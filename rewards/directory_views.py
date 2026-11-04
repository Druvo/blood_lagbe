from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.directory import (
    contact_block_reason,
    contact_states,
    directory_queryset,
    tier_badges,
)
from rewards.geo import DISTRICT_CHOICES, DISTRICT_NAMES
from rewards.leaderboard import display_name
from rewards.models import ContactRequest

User = get_user_model()
PAGE_SIZE = 20


class ContactForm(forms.Form):
    message = forms.CharField(max_length=200, required=False)


def _tier_for(badges_desc, count):
    return next((b for b in badges_desc if count >= b.min_donations), None)


@login_required
def donor_directory(request):
    group = request.GET.get('group', '')
    if group not in dict(BLOOD_GROUP_CHOICES):
        group = ''
    district = request.GET.get('district', '')
    if district not in DISTRICT_NAMES:
        district = ''
    compatible = bool(group) and request.GET.get('compatible') == '1'
    available_only = request.GET.get('available') == '1'

    page = Paginator(
        directory_queryset(
            request.user, group, compatible, district, available_only,
        ),
        PAGE_SIZE,
    ).get_page(request.GET.get('page'))

    badges = tier_badges()
    states = contact_states(request.user, [d.pk for d in page])
    donors = [{
        'user': donor,
        'name': display_name(donor),
        'group': donor.profile.blood_group,
        'district': donor.profile.district,
        'donations': donor.approved_count,
        'badge': _tier_for(badges, donor.approved_count),
        'available': not donor.unavailable,
        'contact': states.get(donor.pk),
    } for donor in page]

    return render(request, 'rewards/directory.html', {
        'donors': donors,
        'page': page,
        'group': group,
        'district': district,
        'compatible': compatible,
        'available_only': available_only,
        'blood_groups': [code for code, _ in BLOOD_GROUP_CHOICES],
        'district_choices': DISTRICT_CHOICES,
        'in_directory': request.user.profile.in_directory,
        'query': request.GET.urlencode(),
    })


@login_required
@require_POST
def contact_donor(request, user_id):
    donor = get_object_or_404(
        User.objects.select_related('profile'), pk=user_id,
    )
    form = ContactForm(request.POST)
    reason = contact_block_reason(request.user, donor)
    if reason or not form.is_valid():
        messages.error(request, reason or 'That message is too long.')
    else:
        ContactRequest.objects.create(
            sender=request.user, donor=donor,
            message=form.cleaned_data['message'].strip(),
        )
        messages.success(
            request,
            'Request sent. Their number is shared only if they accept.',
        )
        if donor.email:
            send_mail(
                'Someone asked for your help on Blood Lagbe',
                f'{display_name(request.user)} would like to reach you about '
                'a blood donation. Review the request, and accept or '
                'decline:\n'
                f'{request.build_absolute_uri(reverse("contact-inbox"))}\n\n'
                'Your phone number is only shared if you accept.',
                settings.DEFAULT_FROM_EMAIL, [donor.email],
                fail_silently=True,
            )

    target = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = reverse('donor-directory')
    return redirect(target)


@login_required
def contact_inbox(request):
    received = list(
        request.user.contact_requests_received
        .select_related('sender').order_by('status', '-created_at')
    )
    sent = list(
        request.user.contact_requests_sent
        .select_related('donor__profile')
    )
    return render(request, 'rewards/contact_inbox.html', {
        'received': [{
            'req': r, 'name': display_name(r.sender),
            # The asker's number is shown once the donor accepts.
            'phone': (
                r.sender.phone
                if r.status == ContactRequest.Status.ACCEPTED else ''
            ),
        } for r in received],
        'sent': [{
            'req': r, 'name': display_name(r.donor),
            'group': r.donor.profile.blood_group,
            'phone': (
                r.donor.phone
                if r.status == ContactRequest.Status.ACCEPTED else ''
            ),
        } for r in sent],
        'pending_count': sum(
            r.status == ContactRequest.Status.PENDING for r in received
        ),
    })


@login_required
@require_POST
def contact_respond(request, pk):
    contact = get_object_or_404(
        ContactRequest, pk=pk, donor=request.user,
    )
    answer = request.POST.get('answer')
    if contact.status != ContactRequest.Status.PENDING:
        messages.error(request, 'You already answered this request.')
    elif answer not in ('accept', 'decline'):
        messages.error(request, 'Choose accept or decline.')
    else:
        contact.status = (
            ContactRequest.Status.ACCEPTED if answer == 'accept'
            else ContactRequest.Status.DECLINED
        )
        contact.answered_at = timezone.now()
        contact.save(update_fields=['status', 'answered_at'])
        messages.success(
            request,
            'Accepted. Your number is now visible to them.'
            if answer == 'accept' else 'Declined. They were not told why.',
        )
    return redirect('contact-inbox')
