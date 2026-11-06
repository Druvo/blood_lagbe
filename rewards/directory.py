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
from django.utils.translation import gettext

from rewards.blood import donor_groups_for
from rewards.models import (
    CONTACT_RETRY_DAYS,
    DONATION_CYCLE_DAYS,
    MAX_CONTACTS_PER_DAY,
    MAX_PENDING_CONTACTS_PER_USER,
    Badge,
    ContactBlock,
    ContactRequest,
    Donation,
)

User = get_user_model()

# Statuses after which the asker waits CONTACT_RETRY_DAYS before asking again.
COOLING_OFF = (ContactRequest.Status.DECLINED, ContactRequest.Status.REVOKED)


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
        # A donor who blocked this viewer is simply not listed for them.
        .exclude(contact_blocks_made__blocked_user=viewer)
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
        if req.status in COOLING_OFF and (
            (req.revoked_at or req.answered_at) is None
            or (req.revoked_at or req.answered_at) < cutoff
        ):
            continue  # old decline or revoke: may ask again
        states[req.donor_id] = req
    return states


def phone_withheld_from(viewer, donor_ids):
    """Donor ids whose phone `viewer` must not see on any page: the donor
    blocked them, or took back a number they had shared (and has not
    accepted them again since). Anonymous or unsaved viewers: empty set."""
    if viewer is None or not getattr(viewer, 'pk', None):
        return set()
    withheld = set(
        ContactBlock.objects.filter(
            blocked_user=viewer, donor__in=donor_ids,
        ).values_list('donor_id', flat=True)
    )
    latest = {}
    for req in ContactRequest.objects.filter(
        sender=viewer, donor__in=donor_ids,
        status__in=(
            ContactRequest.Status.ACCEPTED, ContactRequest.Status.REVOKED,
        ),
    ).order_by('created_at', 'pk'):
        latest[req.donor_id] = req.status
    withheld |= {
        donor_id for donor_id, status in latest.items()
        if status == ContactRequest.Status.REVOKED
    }
    return withheld


def pending_contact_count(sender):
    return ContactRequest.objects.filter(
        sender=sender, status=ContactRequest.Status.PENDING,
    ).count()


def retry_date(request):
    """The day a declined (or revoked) sender may ask again."""
    since = request.revoked_at or request.answered_at
    if since is None:
        return None
    return timezone.localtime(
        since + timedelta(days=CONTACT_RETRY_DAYS),
    ).date()


def contact_block_reason(sender, donor):
    """Why `sender` may not ask `donor` right now, or None if they may."""
    if sender.pk == donor.pk:
        return gettext("You can't contact yourself.")
    if ContactBlock.objects.filter(donor=donor, blocked_user=sender).exists():
        # Deliberately vague: a block is never announced to the blocked member.
        return gettext("This donor can't take requests right now.")
    profile = donor.profile
    if not (donor.is_active and profile.in_directory and profile.blood_group):
        return gettext('This donor is not in the directory.')
    if ContactRequest.objects.filter(
        sender=sender, donor=donor, status=ContactRequest.Status.ACCEPTED,
    ).exists():
        return gettext('This donor already accepted. Their number is in your inbox.')
    pending = ContactRequest.objects.filter(
        sender=sender, status=ContactRequest.Status.PENDING,
    )
    if pending.filter(donor=donor).exists():
        return gettext('You already asked this donor. Please wait for an answer.')
    if pending.count() >= MAX_PENDING_CONTACTS_PER_USER:
        return gettext(
            'You have %(n)s requests waiting for an answer. '
            'Wait for a reply before asking more people.'
        ) % {'n': MAX_PENDING_CONTACTS_PER_USER}
    if ContactRequest.objects.filter(
        sender=sender, created_at__gte=timezone.now() - timedelta(days=1),
    ).count() >= MAX_CONTACTS_PER_DAY:
        return gettext('You have sent a lot of requests today. Please try again tomorrow.')
    cutoff = timezone.now() - timedelta(days=CONTACT_RETRY_DAYS)
    if ContactRequest.objects.filter(
        sender=sender, donor=donor, status=ContactRequest.Status.DECLINED,
        answered_at__gte=cutoff,
    ).exists():
        return gettext(
            'This donor declined recently. You can ask again after '
            '%(n)s days.'
        ) % {'n': CONTACT_RETRY_DAYS}
    if ContactRequest.objects.filter(
        sender=sender, donor=donor, status=ContactRequest.Status.REVOKED,
        revoked_at__gte=cutoff,
    ).exists():
        return gettext(
            "This donor isn't sharing their number any more. You can ask "
            'again after %(n)s days.'
        ) % {'n': CONTACT_RETRY_DAYS}
    return None
