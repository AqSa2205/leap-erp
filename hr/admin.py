from django.contrib import admin
from .models import (Employee, Asset, LeaveType, LeaveEntitlement, LeaveRecord, LeaveExceptionGrant,
                     Holiday, AttendanceSettings, AttendanceRecord, WorkingDay, WFHRecord)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'iqama_number', 'designation', 'nationality', 'deployment', 'contract_type', 'is_active']
    list_filter = ['contract_type', 'nationality', 'deployment', 'is_active']
    search_fields = ['full_name', 'iqama_number', 'work_email', 'mobile_number']


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ['asset_name', 'asset_type', 'serial_number', 'employee_name', 'condition', 'in_stock']
    list_filter = ['asset_type', 'condition', 'in_stock']
    search_fields = ['asset_name', 'serial_number', 'employee_name', 'invoice_number']


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'default_annual_days', 'site_default_annual_days', 'is_paid', 'is_active']


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ['date', 'name', 'is_active']
    list_filter = ['is_active']


@admin.register(LeaveEntitlement)
class LeaveEntitlementAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'year', 'entitled_days']
    list_filter = ['year', 'leave_type']
    search_fields = ['employee__full_name']


@admin.register(LeaveRecord)
class LeaveRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'start_date', 'end_date', 'days']
    list_filter = ['leave_type']
    search_fields = ['employee__full_name']

    # Adding, moving or deleting leave here must re-derive the attendance
    # rows it covers, same as the approval/revoke/delete paths - otherwise a
    # stored 'absent' keeps hiding the leave on the register.
    def save_model(self, request, obj, form, change):
        from hr.attendance_services import sync_attendence_with_leave
        old = LeaveRecord.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        if old is not None:
            sync_attendence_with_leave(old.employee, old.start_date, old.end_date)
        sync_attendence_with_leave(obj.employee, obj.start_date, obj.end_date)

    def delete_model(self, request, obj):
        from hr.attendance_services import sync_attendence_with_leave
        emp, start, end = obj.employee, obj.start_date, obj.end_date
        super().delete_model(request, obj)
        sync_attendence_with_leave(emp, start, end)

    def delete_queryset(self, request, queryset):
        from hr.attendance_services import sync_attendence_with_leave
        ranges = [(lr.employee, lr.start_date, lr.end_date) for lr in queryset.select_related('employee')]
        super().delete_queryset(request, queryset)
        for emp, start, end in ranges:
            sync_attendence_with_leave(emp, start, end)


@admin.register(LeaveExceptionGrant)
class LeaveExceptionGrantAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'year', 'days', 'granted_by', 'granted_at']
    list_filter = ['year', 'leave_type']
    search_fields = ['employee__full_name', 'reason']
    readonly_fields = ['granted_at']


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'status', 'check_in', 'check_out', 'hours_worked']
    list_filter = ['status', 'date']
    search_fields = ['employee__full_name']


@admin.register(WorkingDay)
class WorkingDayAdmin(admin.ModelAdmin):
    list_display = ['date', 'name', 'is_active']
    list_filter = ['is_active']


@admin.register(WFHRecord)
class WFHRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'start_date', 'end_date']
    search_fields = ['employee__full_name']
