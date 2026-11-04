"""Donor directory: who can be found, and who may ask whom for help.

Donors are listed only if they opted in. A listing never carries a phone
number: that is revealed to the asker only after the donor accepts.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import (
    Case, Count, Exists, IntegerField, OuterRef, Q, Value, When,
)
from django.utils import timezone

from rewards.blood import donor_groups_for
from rewards.models import (
    CONTACT_RETRY_DAYS,
    DONATION_CYCLE_DAYS,
    MAX_PENDING_CONTACTS_PER_USER,
    Badge,
    ContactRequest,
    Donation,
)

User = get_user_model()


def directory_queryset(viewer, group='', compatible=False, district='',
                       available_only=False):
    """Opted-in donors matching the filters, available ones first."""
    cutoff = timezone.localdate() - timedelta(days=DONATION_CYCLE_DAYS)
    recent = (
        Donation.objects.filter(donor=OuterRef('pk'), donation_date__gt=cutoff)
        .exclude(status=Donation.Status.REJECTED).order_by()
    )
    qs = (
        User.objects.filter(is_active=True, profile__in_directory=True)
        .exclude(pk=viewer.pk)
        .exclude(profile__blood_group='')
        .select_related('profile')
        .annotate(
            recently_donated=Exists(recent),
            approved_count=Count(
                'donations', filter=Q(donations__status='approved'),
            ),
        )
        .annotate(
            unavailable=Case(
                When(
                    Q(profile__available_to_donate=True)
                    & Q(recently_donated=False),
                    then=Value(0),
                ),
                default=Value(1), output_field=IntegerField(),
            ),
        )
    )
    if group:
        groups = donor_groups_for(group, include_compatible=compatible)
        qs = qs.filter(profile__blood_group__in=groups)
    if district:
        qs = qs.filter(profile__district=district)
    if available_only:
        qs = qs.filter(unavailable=0)
    return qs.order_by('unavailable', '-approved_count', 'pk')


def tier_badges():
    return list(
        Badge.objects.filter(badge_type=Badge.Type.TIER)
        .order_by('-min_donations')
    )


def contact_states(sender, donors):
    """donor id -> the sender's most relevant request to that donor:
    pending, accepted, or a recent decline that blocks asking again."""
    cutoff = timezone.now() - timedelta(days=CONTACT_RETRY_DAYS)
    states = {}
    for req in ContactRequest.objects.filter(
        sender=sender, donor__in=donors,
    ).order_by('created_at'):
        if req.status == ContactRequest.Status.DECLINED and (
            req.answered_at is None or req.answered_at < cutoff
        ):
            continue  # old decline: may ask again
        states[req.donor_id] = req
    return states


def pending_contact_count(sender):
    return ContactRequest.objects.filter(
        sender=sender, status=ContactRequest.Status.PENDING,
    ).count()


def retry_date(request):
    """The day a declined sender may ask again."""
    if request.answered_at is None:
        return None
    return timezone.localtime(
        request.answered_at + timedelta(days=CONTACT_RETRY_DAYS),
    ).date()


def contact_block_reason(sender, donor):
    """Why `sender` may not ask `donor` right now, or None if they may."""
    if sender.pk == donor.pk:
        return "You can't contact yourself."
    profile = donor.profile
    if not (donor.is_active and profile.in_directory and profile.blood_group):
        return 'This donor is not in the directory.'
    pending = ContactRequest.objects.filter(
        sender=sender, status=ContactRequest.Status.PENDING,
    )
    if pending.filter(donor=donor).exists():
        return 'You already asked this donor. Please wait for an answer.'
    if pending.count() >= MAX_PENDING_CONTACTS_PER_USER:
        return (
            f'You have {MAX_PENDING_CONTACTS_PER_USER} requests waiting for '
            'an answer. Wait for a reply before asking more people.'
        )
    cutoff = timezone.now() - timedelta(days=CONTACT_RETRY_DAYS)
    if ContactRequest.objects.filter(
        sender=sender, donor=donor, status=ContactRequest.Status.DECLINED,
        answered_at__gte=cutoff,
    ).exists():
        return (
            f'This donor declined recently. You can ask again after '
            f'{CONTACT_RETRY_DAYS} days.'
        )
    return None
