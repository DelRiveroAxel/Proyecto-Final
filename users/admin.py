from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Role, User, UserRole


@admin.register(User)
class IurkoUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'is_active')


admin.site.register(Role)
admin.site.register(UserRole)
