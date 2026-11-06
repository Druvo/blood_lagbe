from django.contrib import admin

from moments.models import (
    Celebration,
    ImpactUpdate,
    ReminderPref,
    StreakFreeze,
    ThankNote,
)


@admin.register(ImpactUpdate)
class ImpactUpdateAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user', 'blood_request', 'blood_group', 'district',
        'dismissed', 'emailed_at', 'created_at',
    )
    list_filter = ('dismissed', 'blood_group')
    raw_id_fields = ('user', 'blood_request')
    date_hierarchy = 'created_at'


@admin.register(ThankNote)
class ThankNoteAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'blood_request', 'sender', 'recipient', 'short_text',
        'to_all', 'hidden', 'created_at',
    )
    list_filter = ('hidden', 'to_all')
    list_editable = ('hidden',)
    search_fields = ('text',)
    raw_id_fields = ('blood_request', 'sender', 'recipient')

    @admin.display(description='Note')
    def short_text(self, obj):
        return obj.text[:60]


@admin.register(ReminderPref)
class ReminderPrefAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'email_enabled', 'impact_email', 'last_reminded_cycle',
        'updated_at',
    )
    list_filter = ('email_enabled', 'impact_email')
    raw_id_fields = ('user',)


@admin.register(Celebration)
class CelebrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'key', 'created_at')
    list_filter = ('key',)
    raw_id_fields = ('user',)


@admin.register(StreakFreeze)
class StreakFreezeAdmin(admin.ModelAdmin):
    list_display = ('user', 'year', 'gap_start', 'gap_end', 'created_at')
    list_filter = ('year',)
    raw_id_fields = ('user',)
