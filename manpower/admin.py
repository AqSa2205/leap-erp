from django.contrib import admin
from .models import ManpowerSheet, ManpowerLineItem


class ManpowerLineItemInline(admin.TabularInline):
    model = ManpowerLineItem
    extra = 1
    fields = [
        'order', 'employee_name', 'department', 'designation',
        'gross_salary', 'iqama_cost', 'gosi_cost', 'eosb',
    ]


@admin.register(ManpowerSheet)
class ManpowerSheetAdmin(admin.ModelAdmin):
    list_display = ['title', 'project_reference', 'date', 'created_by', 'created_at']
    list_filter = ['date']
    search_fields = ['title', 'project_reference']
    ordering = ['-date']
    inlines = [ManpowerLineItemInline]


@admin.register(ManpowerLineItem)
class ManpowerLineItemAdmin(admin.ModelAdmin):
    list_display = [
        'employee_name', 'sheet', 'department', 'designation',
        'gross_salary', 'gosi_cost',
    ]
    list_filter = ['sheet', 'department']
    search_fields = ['employee_name', 'designation']
