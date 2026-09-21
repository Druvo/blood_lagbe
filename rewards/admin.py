from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from rewards.models import (
    Badge,
    Donation,
    get_donor_badge_snapshot,
    get_newly_unlocked_badges,
)

User = get_user_model()


def _notify_new_badges(donor, badges):
    if not donor.email or not badges:
        return
    lines = '\n'.join(f'- {b.name}: {b.description}' for b in badges)
    send_mail(
        subject="You've unlocked a new Blood Lagbe badge!",
        message=(
            f"Congratulations! Your latest verified donation unlocked:\n\n"
            f"{lines}\n\nKeep donating to unlock more. Thank you for "
            f"saving lives."
        ),
        from_email=None,
        recipient_list=[donor.email],
        fail_silently=True,
    )


@admin.action(description='Approve selected donations')
def approve_donations(modeladmin, request, queryset):
    queryset = queryset.exclude(status=Donation.Status.APPROVED)
    donors = list(User.objects.filter(
        id__in=queryset.values_list('donor_id', flat=True).distinct(),
    ))
    before = {donor.id: get_donor_badge_snapshot(donor) for donor in donors}

    queryset.update(
        status=Donation.Status.APPROVED, verified_by=request.user,
        updated_at=timezone.now(),
    )

    notified = 0
    for donor in donors:
        newly_unlocked = get_newly_unlocked_badges(before[donor.id], donor)
        if newly_unlocked:
            _notify_new_badges(donor, newly_unlocked)
            notified += 1

    if notified:
        modeladmin.message_user(
            request, f'Sent badge-unlock notifications to {notified} donor(s).',
        )


@admin.action(description='Reject selected donations')
def reject_donations(modeladmin, request, queryset):
    queryset.exclude(status=Donation.Status.REJECTED).update(
        status=Donation.Status.REJECTED, verified_by=request.user,
        updated_at=timezone.now(),
    )


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = (
        'donor', 'blood_group', 'donation_date', 'location',
        'status', 'verified_by',
    )
    list_filter = ('status', 'blood_group')
    search_fields = ('donor__phone', 'donor__email', 'location')
    actions = [approve_donations, reject_donations]


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ('name', 'badge_type', 'min_donations', 'min_streak', 'icon', 'color')
    list_filter = ('badge_type',)
