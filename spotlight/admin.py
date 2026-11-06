from django.contrib import admin

from rewards.leaderboard import display_name
from spotlight.models import MonthlyAward


@admin.register(MonthlyAward)
class MonthlyAwardAdmin(admin.ModelAdmin):
    list_display = ('period', 'kind', 'winner', 'count', 'awarded_at')
    list_filter = ('kind', 'year')
    raw_id_fields = ('user', 'squad')
    date_hierarchy = 'awarded_at'

    @admin.display(description='Month')
    def period(self, obj):
        return f'{obj.year}-{obj.month:02d}'

    @admin.display(description='Winner')
    def winner(self, obj):
        if obj.squad_id:
            return obj.squad.name
        return display_name(obj.user) if obj.user_id else ''

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'squad')
