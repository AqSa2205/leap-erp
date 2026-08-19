from django.contrib import admin

from .models import (
    Account, AccountingSettings, Document, DocumentLine, Partner, Voucher,
    VoucherLine,
)


@admin.register(AccountingSettings)
class AccountingSettingsAdmin(admin.ModelAdmin):
    """Singleton — the control accounts documents post against."""
    raw_id_fields = ('default_receivable_account', 'default_payable_account',
                     'output_tax_account', 'input_tax_account')

    def has_add_permission(self, request):
        # One row only; edit the existing one rather than creating a second.
        return not AccountingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'internal_type', 'parent', 'is_active')
    list_filter = ('internal_type', 'is_active')
    search_fields = ('code', 'name')
    ordering = ('code',)
    raw_id_fields = ('parent',)
    readonly_fields = ('source_row', 'created_at', 'updated_at')


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'vat_number', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('name', 'vat_number', 'cr_number', 'zoho_contact_id')
    raw_id_fields = ('receivable_account', 'payable_account')


class VoucherLineInline(admin.TabularInline):
    model = VoucherLine
    extra = 2
    raw_id_fields = ('account', 'partner', 'project')


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'voucher_type', 'date', 'partner', 'amount', 'status')
    list_filter = ('voucher_type', 'status', 'source')
    search_fields = ('number', 'narration', 'zoho_id')
    date_hierarchy = 'date'
    raw_id_fields = ('partner', 'project')
    inlines = [VoucherLineInline]

    @admin.display(description='Amount')
    def amount(self, obj):
        return obj.total_debit


class DocumentLineInline(admin.TabularInline):
    model = DocumentLine
    extra = 2
    raw_id_fields = ('account', 'project')


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('number', 'kind', 'partner', 'date', 'due_date', 'total',
                    'amount_paid', 'status')
    list_filter = ('kind', 'status', 'source')
    search_fields = ('number', 'reference', 'partner__name', 'zoho_id')
    date_hierarchy = 'date'
    raw_id_fields = ('partner', 'project')
    inlines = [DocumentLineInline]
