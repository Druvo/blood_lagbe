from django.contrib import admin
from django.utils import timezone

from rewards.models import Donation


@admin.action(description='Approve selected donations')
def approve_donations(modeladmin, request, queryset):
    queryset.exclude(status=Donation.Status.APPROVED).update(
        status=Donation.Status.APPROVED, verified_by=request.user,
        updated_at=timezone.now(),
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
