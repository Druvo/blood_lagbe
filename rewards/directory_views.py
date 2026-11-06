from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.utils.translation import gettext

from rewards.blood import BLOOD_GROUP_CHOICES
from rewards.directory import (
    COOLING_OFF,
    contact_block_reason,
    contact_states,
    directory_queryset,
    pending_contact_count,
    retry_date,
    tier_badges,
)
from rewards.geo import DISTRICT_CHOICES, DISTRICT_NAMES
from rewards.leaderboard import display_name, initials
from rewards.models import (
    MAX_PENDING_CONTACTS_PER_USER,
    ContactBlock,
    ContactRequest,
)

User = get_user_model()
PAGE_SIZE = 20


class ContactForm(forms.Form):
    message = forms.CharField(max_length=200, required=False)


def _tier_for(badges_desc, count):
    return next((b for b in badges_desc if count >= b.min_donations), None)


def _safe_target(request):
    target = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = reverse('donor-directory')
    return target


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
    pending_total = pending_contact_count(request.user)
    cap_reached = pending_total >= MAX_PENDING_CONTACTS_PER_USER
    donors = []
    for donor in page:
        contact = states.get(donor.pk)
        reason = ''
        if contact is None and cap_reached:
            reason = (
                f'You have {MAX_PENDING_CONTACTS_PER_USER} requests waiting '
                'for an answer. Wait for a reply before asking more people.'
            )
        donors.append({
            'user': donor,
            'name': display_name(donor),
            'initials': initials(display_name(donor)),
            'group': donor.profile.blood_group,
            'district': donor.profile.district,
            'donations': donor.approved_count,
            'badge': _tier_for(badges, donor.approved_count),
            'available': not donor.unavailable,
            'contact': contact,
            'retry_on': (
                retry_date(contact)
                if contact and contact.status in COOLING_OFF
                else None
            ),
            'reason': reason,
            'compatible_only': bool(compatible and group
                                    and donor.profile.blood_group != group),
        })

    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'rewards/directory.html', {
        'donors': donors,
        'page': page,
        'total': page.paginator.count,
        'group': group,
        'district': district,
        'compatible': compatible,
        'available_only': available_only,
        'has_filters': bool(group or district or available_only),
        'blood_groups': [code for code, _ in BLOOD_GROUP_CHOICES],
        'district_choices': DISTRICT_CHOICES,
        'in_directory': request.user.profile.in_directory,
        'cap_reached': cap_reached,
        'max_pending': MAX_PENDING_CONTACTS_PER_USER,
        'query': params.urlencode(),
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
        try:
            with transaction.atomic():
                ContactRequest.objects.create(
                    sender=request.user, donor=donor,
                    message=form.cleaned_data['message'].strip(),
                )
        except IntegrityError:
            # A double click raced past the check: the first one won.
            messages.info(
                request, gettext('You already asked this donor. Please wait for an answer.'),
            )
            return redirect(_safe_target(request))
        messages.success(
            request,
            gettext('Request sent. Their number is shared only if they accept.'),
        )
        if donor.email:
            send_mail(
                f'Someone asked for your help on {settings.SITE_NAME}',
                f'{display_name(request.user)} would like to reach you about '
                'a blood donation. Review the request, and accept or '
                'decline:\n'
                f'{request.build_absolute_uri(reverse("contact-inbox"))}\n\n'
                'Your phone number is only shared if you accept.',
                settings.DEFAULT_FROM_EMAIL, [donor.email],
                fail_silently=True,
            )

    return redirect(_safe_target(request))


@login_required
def contact_inbox(request):
    Status = ContactRequest.Status
    # Waiting requests first so they are never buried, then newest first.
    received = sorted(
        request.user.contact_requests_received.select_related('sender'),
        key=lambda r: (r.status != Status.PENDING, -r.created_at.timestamp()),
    )
    sent = sorted(
        request.user.contact_requests_sent.select_related('donor__profile'),
        key=lambda r: (r.status != Status.PENDING, -r.created_at.timestamp()),
    )
    blocked_ids = set(
        request.user.contact_blocks_made.values_list('blocked_user_id', flat=True)
    )
    # Donors who blocked me: never hint that I may ask them again.
    blocked_me = set(
        request.user.contact_blocks_received.values_list('donor_id', flat=True)
    )
    return render(request, 'rewards/contact_inbox.html', {
        'received': [{
            'req': r, 'name': display_name(r.sender),
            'initials': initials(display_name(r.sender)),
            # The asker's number is shown once the donor accepts.
            'phone': r.sender.phone if r.status == Status.ACCEPTED else '',
            'blocked': r.sender_id in blocked_ids,
        } for r in received],
        'sent': [{
            'req': r, 'name': display_name(r.donor),
            'initials': initials(display_name(r.donor)),
            'group': r.donor.profile.blood_group,
            'phone': r.donor.phone if r.status == Status.ACCEPTED else '',
            'retry_on': (
                retry_date(r)
                if r.status in COOLING_OFF and r.donor_id not in blocked_me
                else None
            ),
        } for r in sent],
        'blocked': [{
            'block': b, 'name': display_name(b.blocked_user),
        } for b in request.user.contact_blocks_made.select_related(
            'blocked_user',
        )],
        'pending_count': sum(r.status == Status.PENDING for r in received),
        'sent_waiting': sum(r.status == Status.PENDING for r in sent),
    })


@login_required
@require_POST
def contact_respond(request, pk):
    contact = get_object_or_404(
        ContactRequest, pk=pk, donor=request.user,
    )
    answer = request.POST.get('answer')
    if contact.status != ContactRequest.Status.PENDING:
        messages.error(request, gettext('You already answered this request.'))
    elif answer not in ('accept', 'decline'):
        messages.error(request, gettext('Choose accept or decline.'))
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


@login_required
@require_POST
def contact_revoke(request, pk):
    """The donor takes back a number they shared. The phone is hidden again
    at once everywhere it was shown. The asker sees a neutral note."""
    contact = get_object_or_404(
        ContactRequest, pk=pk, donor=request.user,
    )
    # Every accepted request from this person: one click hides the number
    # for good, however many times they asked.
    changed = ContactRequest.objects.filter(
        sender_id=contact.sender_id, donor=request.user,
        status=ContactRequest.Status.ACCEPTED,
    ).update(
        status=ContactRequest.Status.REVOKED, revoked_at=timezone.now(),
    )
    if changed:
        messages.success(
            request,
            gettext('Done. Your number is hidden again. They were not given a reason.'),
        )
    else:
        messages.info(request, gettext('You were not sharing your number with them.'))
    return redirect('contact-inbox')


@login_required
@require_POST
def contact_block(request, pk):
    """Stop all contact from one asker: pending requests are declined,
    shared numbers are taken back, and they cannot ask again."""
    contact = get_object_or_404(
        ContactRequest, pk=pk, donor=request.user,
    )
    now = timezone.now()
    with transaction.atomic():
        ContactBlock.objects.get_or_create(
            donor=request.user, blocked_user_id=contact.sender_id,
        )
        mine = ContactRequest.objects.filter(
            sender_id=contact.sender_id, donor=request.user,
        )
        mine.filter(status=ContactRequest.Status.PENDING).update(
            status=ContactRequest.Status.DECLINED, answered_at=now,
        )
        mine.filter(status=ContactRequest.Status.ACCEPTED).update(
            status=ContactRequest.Status.REVOKED, revoked_at=now,
        )
    messages.success(
        request,
        gettext('Blocked. They cannot send you requests or see your number, and '
        'they were not told.'),
    )
    return redirect('contact-inbox')


@login_required
@require_POST
def contact_unblock(request, pk):
    block = get_object_or_404(ContactBlock, pk=pk, donor=request.user)
    block.delete()
    messages.success(request, gettext('Unblocked. They can send you requests again.'))
    return redirect('contact-inbox')
