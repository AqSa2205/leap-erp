from django.contrib import admin

from .models import ChargeRate, CostBasis, ManpowerCostLine, ManpowerCostSheet


@admin.register(CostBasis)
class CostBasisAdmin(admin.ModelAdmin):
    list_display = ('name', 'overhead_pct', 'profit_pct', 'billable_months',
                    'hours_per_month', 'working_days_per_month', 'is_default')
    list_filter = ('is_default',)
    search_fields = ('name',)


class ManpowerCostLineInline(admin.TabularInline):
    model = ManpowerCostLine
    extra = 0
    # raw id for employee (thousands of rows); the resource catalogue is ~26
    # entries, so a plain dropdown is friendlier and needs no admin of its own.
    raw_id_fields = ('employee',)
    fields = ('order', 'employee', 'employee_name', 'position', 'designation',
              'department', 'classification', 'gross_salary')


@admin.register(ManpowerCostSheet)
class ManpowerCostSheetAdmin(admin.ModelAdmin):
    list_display = ('title', 'project_reference', 'date', 'basis')
    list_filter = ('basis', 'date')
    search_fields = ('title', 'project_reference')
    inlines = [ManpowerCostLineInline]


@admin.register(ChargeRate)
class ChargeRateAdmin(admin.ModelAdmin):
    list_display = ('position', 'classification', 'basis', 'monthly_cost',
                    'manual_rate', 'updated_at')
    list_filter = ('basis', 'classification')
    search_fields = ('position__name',)
