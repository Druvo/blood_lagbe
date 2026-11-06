from django.contrib import admin

from habits.models import DailyCompletion, DigestLog, DigestPref, ShareAction


@admin.register(ShareAction)
class ShareActionAdmin(admin.ModelAdmin):
    list_display = ('user', 'blood_request', 'day', 'created_at')
    list_filter = ('day',)
    raw_id_fields = ('user', 'blood_request')
    date_hierarchy = 'day'


@admin.register(DailyCompletion)
class DailyCompletionAdmin(admin.ModelAdmin):
    list_display = ('user', 'day', 'task', 'hour', 'created_at')
    list_filter = ('task', 'day')
    raw_id_fields = ('user',)
    date_hierarchy = 'day'


@admin.register(DigestPref)
class DigestPrefAdmin(admin.ModelAdmin):
    list_display = ('user', 'enabled', 'last_sent_week', 'updated_at')
    list_filter = ('enabled',)
    raw_id_fields = ('user',)


@admin.register(DigestLog)
class DigestLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'iso_year', 'iso_week', 'points', 'level', 'sent_at')
    list_filter = ('iso_year',)
    raw_id_fields = ('user',)
