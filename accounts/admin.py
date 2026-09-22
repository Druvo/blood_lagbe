from django.contrib import admin
from accounts import models


@admin.register(models.User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        'email', 'first_name', 'last_name', 'phone', 'last_login',
        'is_active', 'staff',
    ]
    list_filter = ['admin', 'staff', 'is_active']
    fieldsets = (
        (None, {'fields': ('email', 'phone', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name')}),
        ('Permissions', {'fields': ('staff', 'admin', 'is_active')}),
        ('Dates', {'fields': ('last_login',)}),
    )
    search_fields = ['id', 'email', 'first_name', 'last_name', 'phone']
    ordering = ['email']


admin.site.register(models.Profile)
