from django.contrib import admin
from .models import ExchangeRate, CostingSheet, CostingSection, CostingLineItem


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ['currency_code', 'currency_name', 'rate_to_usd', 'updated_at']
    search_fields = ['currency_code', 'currency_name']


class CostingSectionInline(admin.TabularInline):
    model = CostingSection
    extra = 0


@admin.register(CostingSheet)
class CostingSheetAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'status', 'margin', 'output_currency', 'created_by', 'updated_at']
    list_filter = ['status', 'output_currency']
    search_fields = ['title', 'customer_reference']
    raw_id_fields = ['project', 'created_by']
    inlines = [CostingSectionInline]


class CostingLineItemInline(admin.TabularInline):
    model = CostingLineItem
    extra = 0


@admin.register(CostingSection)
class CostingSectionAdmin(admin.ModelAdmin):
    list_display = ['section_number', 'title', 'costing_sheet', 'order']
    list_filter = ['costing_sheet']
    inlines = [CostingLineItemInline]


@admin.register(CostingLineItem)
class CostingLineItemAdmin(admin.ModelAdmin):
    list_display = ['item_number', 'description', 'quantity', 'unit', 'base_price', 'supplier_currency']
    list_filter = ['unit', 'supplier_currency']
    search_fields = ['description', 'item_number', 'make', 'model_number']
