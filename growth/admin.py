from django.contrib import admin

from growth.models import Invite, Referral, ReferralReward, VerifiedHospital


@admin.register(VerifiedHospital)
class VerifiedHospitalAdmin(admin.ModelAdmin):
    list_display = ('name', 'district', 'place', 'created_at')
    list_filter = ('district',)
    search_fields = ('name', 'notes')
    raw_id_fields = ('place',)


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ('code', 'user_id', 'created_at')
    search_fields = ('code',)
    readonly_fields = ('user', 'code', 'created_at')

    def has_add_permission(self, request):
        return False


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ('inviter_id', 'invitee_id', 'created_at', 'rewarded_at')
    list_filter = ('rewarded_at',)
    readonly_fields = ('inviter', 'invitee', 'created_at')

    def has_add_permission(self, request):
        return False


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = ('user_id', 'level', 'awarded_at')
    list_filter = ('level',)
    readonly_fields = ('awarded_at',)
    raw_id_fields = ('user',)
