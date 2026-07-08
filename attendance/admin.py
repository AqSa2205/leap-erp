from django.contrib import admin

from .models import OfficeNetwork, RegisteredDevice, Heartbeat, AttendanceDay


@admin.register(OfficeNetwork)
class OfficeNetworkAdmin(admin.ModelAdmin):
    list_display = ('label', 'bssid', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('label', 'bssid')


@admin.register(RegisteredDevice)
class RegisteredDeviceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'label', 'token', 'is_active', 'last_seen_at')
    list_filter = ('is_active',)
    search_fields = ('employee__full_name', 'employee__iqama_number', 'label', 'token')
    readonly_fields = ('token', 'last_seen_at', 'created_at')
    autocomplete_fields = ('employee',)


@admin.register(Heartbeat)
class HeartbeatAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'device', 'bssid', 'idle_seconds', 'counted', 'reason')
    list_filter = ('counted', 'reason')
    search_fields = ('device__employee__full_name', 'bssid', 'hostname')
    readonly_fields = ('device', 'bssid', 'idle_seconds', 'counted', 'reason', 'hostname', 'created_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False


@admin.register(AttendanceDay)
class AttendanceDayAdmin(admin.ModelAdmin):
    list_display = ('date', 'employee', 'is_present', 'active_minutes', 'first_seen', 'last_seen')
    list_filter = ('is_present', 'date')
    search_fields = ('employee__full_name', 'employee__iqama_number')
    date_hierarchy = 'date'
    readonly_fields = ('employee', 'date', 'first_seen', 'last_seen', 'active_minutes', 'is_present', 'updated_at')
