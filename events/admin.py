from django.contrib import admin, messages
from django.utils import timezone

from events.models import (
    Campaign,
    CampaignBadgeAward,
    Challenge,
    Event,
    EventRSVP,
    EventTeam,
    VerifiedOrganizer,
)


class RSVPInline(admin.TabularInline):
    model = EventRSVP
    extra = 0
    raw_id_fields = ('user', 'verified_by')
    fields = ('user', 'share_name', 'checked_in', 'verified', 'verified_by')


class TeamInline(admin.TabularInline):
    model = EventTeam
    extra = 0
    raw_id_fields = ('squad', 'entered_by')


@admin.action(description='Approve selected events')
def approve_events(modeladmin, request, queryset):
    updated = queryset.filter(status=Event.Status.PENDING).update(
        status=Event.Status.APPROVED, approved_at=timezone.now(),
        updated_at=timezone.now(),
    )
    modeladmin.message_user(request, f'Approved {updated} event(s).')


@admin.action(description='Cancel selected events')
def cancel_events(modeladmin, request, queryset):
    updated = queryset.exclude(status=Event.Status.CANCELLED).update(
        status=Event.Status.CANCELLED, updated_at=timezone.now(),
    )
    modeladmin.message_user(request, f'Cancelled {updated} event(s).')


@admin.action(description='Mark selected events completed')
def complete_events(modeladmin, request, queryset):
    updated = queryset.filter(status=Event.Status.APPROVED).update(
        status=Event.Status.COMPLETED, updated_at=timezone.now(),
    )
    modeladmin.message_user(request, f'Completed {updated} event(s).')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'organizer', 'district', 'starts_at', 'status', 'capacity',
    )
    list_filter = ('status', 'district')
    search_fields = ('title', 'organizer_name', 'venue')
    date_hierarchy = 'starts_at'
    raw_id_fields = ('organizer', 'place')
    readonly_fields = ('approved_at', 'created_at', 'updated_at')
    actions = [approve_events, cancel_events, complete_events]
    inlines = [RSVPInline, TeamInline]

    def save_model(self, request, obj, form, change):
        if obj.status == Event.Status.APPROVED and not obj.approved_at:
            obj.approved_at = timezone.now()
        super().save_model(request, obj, form, change)


@admin.action(description='Mark selected as verified attendance')
def verify_attendance(modeladmin, request, queryset):
    updated = queryset.filter(checked_in=True).update(
        verified=True, verified_by=request.user,
    )
    skipped = queryset.count() - updated
    modeladmin.message_user(request, f'Verified {updated} attendance record(s).')
    if skipped:
        modeladmin.message_user(
            request, f'{skipped} skipped: the organizer has not checked them in.',
            level=messages.WARNING,
        )


@admin.register(EventRSVP)
class EventRSVPAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'checked_in', 'verified', 'created_at')
    list_filter = ('checked_in', 'verified')
    raw_id_fields = ('event', 'user', 'verified_by')
    actions = [verify_attendance]


@admin.register(VerifiedOrganizer)
class VerifiedOrganizerAdmin(admin.ModelAdmin):
    list_display = ('user', 'note', 'created_at')
    raw_id_fields = ('user',)


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'is_active', 'starts_on', 'ends_on', 'recurring_month',
        'recurring_day', 'badge_name',
    )
    list_filter = ('is_active',)
    list_editable = ('is_active',)
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)


@admin.register(CampaignBadgeAward)
class CampaignBadgeAwardAdmin(admin.ModelAdmin):
    list_display = ('campaign', 'user', 'awarded_at')
    list_filter = ('campaign',)
    raw_id_fields = ('user', 'donation')


@admin.register(Challenge)
class ChallengeAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'scope', 'starts_on', 'ends_on', 'target_donations',
        'is_published',
    )
    list_filter = ('scope', 'is_published')
    search_fields = ('title',)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
