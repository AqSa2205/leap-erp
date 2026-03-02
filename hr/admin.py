from django.contrib import admin
from .models import Employee, Asset


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
