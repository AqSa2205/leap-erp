from django.contrib import admin

from .models import CompanyDocument


@admin.register(CompanyDocument)
class CompanyDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'document_type', 'reference_number',
                    'issue_date', 'expiry_date', 'uploaded_at')
    list_filter = ('document_type',)
    search_fields = ('title', 'issuing_authority', 'reference_number')
