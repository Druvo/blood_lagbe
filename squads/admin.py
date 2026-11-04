from django.contrib import admin

from squads.models import Squad, SquadMember


class MemberInline(admin.TabularInline):
    model = SquadMember
    extra = 0
    raw_id_fields = ('user',)


@admin.register(Squad)
class SquadAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'is_public', 'district', 'created_at')
    list_filter = ('is_public', 'district')
    search_fields = ('name',)
    raw_id_fields = ('owner',)
    readonly_fields = ('join_code',)
    inlines = [MemberInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # The owner is always a member, so the squad page works for them.
        SquadMember.objects.get_or_create(
            squad=obj, user=obj.owner,
            defaults={'role': SquadMember.Role.OWNER},
        )
