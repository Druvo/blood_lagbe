from django.contrib import admin

from alerts.models import AlertPreference, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'kind', 'user', 'created_at', 'read_at')
    list_filter = ('kind',)
    search_fields = ('title',)
    raw_id_fields = ('user',)


@admin.register(AlertPreference)
class AlertPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'enabled', 'email_enabled', 'only_rare_groups', 'daily_cap')
    list_filter = ('enabled', 'email_enabled', 'only_rare_groups')
    raw_id_fields = ('user',)
