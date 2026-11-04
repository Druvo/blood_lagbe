from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from rewards.models import (
    Badge,
    BloodRequest,
    Donation,
    Place,
    RequestReport,
    RequestResponse,
    RequestUpdate,
    get_donor_badge_snapshot,
    get_newly_unlocked_badges,
)
from rewards.request_notify import close_and_notify

User = get_user_model()


def _notify_new_badges(donor, badges):
    if not donor.email or not badges:
        return
    lines = '\n'.join(f'- {b.name}: {b.description}' for b in badges)
    send_mail(
        subject=f"You've unlocked a new {settings.SITE_NAME} badge!",
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
        'donor', 'blood_group', 'donation_date', 'location', 'district',
        'status', 'blood_request', 'verified_by',
    )
    list_filter = ('status', 'blood_group', 'district')
    search_fields = ('donor__phone', 'donor__email', 'location')
    actions = [approve_donations, reject_donations]


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'badge_type', 'min_donations', 'min_streak',
        'min_request_donations', 'icon', 'color',
    )
    list_filter = ('badge_type',)


@admin.action(description='Close selected requests')
def close_requests(modeladmin, request, queryset):
    closed = 0
    for blood_request in queryset.exclude(
        status=BloodRequest.Status.CLOSED,
    ):
        url = request.build_absolute_uri(
            reverse('request-detail', args=[blood_request.pk]),
        )
        closed += close_and_notify(blood_request, blood_request.outcome, url)
    modeladmin.message_user(request, f'Closed {closed} request(s).')


@admin.action(description='Re-open selected requests')
def reopen_requests(modeladmin, request, queryset):
    queryset.update(
        status=BloodRequest.Status.OPEN, outcome='', closed_at=None,
    )


@admin.action(description='Clear reports (show requests on the board again)')
def clear_reports(modeladmin, request, queryset):
    cleared = RequestReport.objects.filter(
        blood_request__in=queryset, cleared=False,
    ).update(cleared=True)
    modeladmin.message_user(request, f'Cleared {cleared} report(s).')


class RequestUpdateInline(admin.TabularInline):
    model = RequestUpdate
    extra = 0
    fields = ('kind', 'text', 'author', 'created_at')
    readonly_fields = ('kind', 'text', 'author', 'created_at')
    can_delete = True

    def has_add_permission(self, request, obj=None):
        return False


class RequestReportInline(admin.TabularInline):
    model = RequestReport
    extra = 0
    fields = ('reason', 'text', 'reporter', 'cleared', 'created_at')
    readonly_fields = ('reason', 'text', 'reporter', 'created_at')

    def has_add_permission(self, request, obj=None):
        return False


class FlaggedFilter(admin.SimpleListFilter):
    title = 'reports'
    parameter_name = 'flagged'

    def lookups(self, request, model_admin):
        return [('yes', 'Flagged (hidden from board)'),
                ('any', 'Has open reports')]

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.flagged()
        if self.value() == 'any':
            return queryset.with_report_count().filter(report_total__gt=0)
        return queryset


@admin.register(BloodRequest)
class BloodRequestAdmin(admin.ModelAdmin):
    list_display = (
        'blood_group', 'hospital', 'district', 'units_needed', 'needed_by',
        'status', 'outcome', 'open_reports', 'flagged', 'requester',
        'created_at',
    )
    list_filter = ('status', 'outcome', FlaggedFilter, 'blood_group', 'district')
    search_fields = ('hospital', 'district', 'requester__phone', 'note')
    readonly_fields = ('created_at', 'closed_at')
    inlines = [RequestUpdateInline, RequestReportInline]
    actions = [close_requests, reopen_requests, clear_reports]

    def get_queryset(self, request):
        return super().get_queryset(request).with_report_count()

    @admin.display(description='Open reports', ordering='report_total')
    def open_reports(self, obj):
        return obj.report_total

    @admin.display(description='Flagged', boolean=True)
    def flagged(self, obj):
        return obj.is_flagged


@admin.register(RequestReport)
class RequestReportAdmin(admin.ModelAdmin):
    list_display = (
        'blood_request', 'reason', 'reporter', 'cleared', 'created_at',
    )
    list_filter = ('reason', 'cleared')
    search_fields = ('blood_request__hospital', 'reporter__phone', 'text')


@admin.register(RequestResponse)
class RequestResponseAdmin(admin.ModelAdmin):
    list_display = ('blood_request', 'donor', 'created_at')
    search_fields = ('donor__phone', 'blood_request__hospital')


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'district', 'kind', 'is_approved')
    list_filter = ('kind', 'is_approved', 'district')
    search_fields = ('name', 'district')
