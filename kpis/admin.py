from django.contrib import admin

from .models import KPIEntry


@admin.register(KPIEntry)
class KPIEntryAdmin(admin.ModelAdmin):
    list_display = ('period', 'kpi_key', 'target', 'manual_value', 'updated_by', 'updated_at')
    list_filter = ('period',)
    search_fields = ('kpi_key', 'period')
