from django.contrib import admin
from .models import Employee, Asset, LeaveType, LeaveEntitlement, LeaveRecord, Holiday, AttendanceSettings, AttendanceRecord


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
    list_display = ['name', 'code', 'default_annual_days', 'is_paid', 'is_active']


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


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'status', 'check_in', 'check_out', 'hours_worked']
    list_filter = ['status', 'date']
    search_fields = ['employee__full_name']
