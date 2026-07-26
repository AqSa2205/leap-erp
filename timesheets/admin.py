from django.contrib import admin
from .models import ActivityCode, TimesheetEntry, TimesheetMonth


@admin.register(ActivityCode)
class ActivityCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'label', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code', 'label')


@admin.register(TimesheetEntry)
class TimesheetEntryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'date', 'activity_code', 'hours', 'status')
    list_filter = ('status', 'activity_code')
    search_fields = ('employee__full_name', 'task_description')


@admin.register(TimesheetMonth)
class TimesheetMonthAdmin(admin.ModelAdmin):
    list_display = ('employee', 'year', 'month', 'is_submitted', 'submitted_at', 'reopened_at')
    list_filter = ('year', 'month')
    search_fields = ('employee__full_name',)

from .models import HRSettings, TimesheetRequest, TimesheetRequestAck


@admin.register(HRSettings)
class HRSettingsAdmin(admin.ModelAdmin):
    list_display = ('hr_email',)

    def has_add_permission(self, request):
        # Singleton — block creating a second row from the admin "Add" button.
        return not HRSettings.objects.exists()


@admin.register(TimesheetRequest)
class TimesheetRequestAdmin(admin.ModelAdmin):
    list_display = ('year', 'month', 'requested_by', 'requested_at')
    list_filter = ('year', 'month')


@admin.register(TimesheetRequestAck)
class TimesheetRequestAckAdmin(admin.ModelAdmin):
    list_display = ('request', 'employee', 'acknowledged_at')