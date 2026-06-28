from django.contrib import admin

from .models import QuotationImport


@admin.register(QuotationImport)
class QuotationImportAdmin(admin.ModelAdmin):
    list_display = ['original_filename', 'status', 'model_used', 'purchase_order', 'created_by', 'created_at']
    list_filter = ['status']
    search_fields = ['original_filename']
    readonly_fields = ['extracted_data', 'model_used', 'error', 'created_at', 'updated_at']
