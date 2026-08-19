from django.contrib import admin

from .models import (
    Account, AccountingSettings, Document, DocumentLine, Partner, Voucher,
    VoucherLine,
)


def can_use_accounting(user):
    """Accounting is the finance team's, plus super admin.

    The same rule the views enforce (accounting.views._can_view_accounting).
    Django superusers are included as the escape hatch — locking the person
    who administers the system out of it creates a worse problem than it
    solves.
    """
    return bool(
        getattr(user, 'is_superuser', False)
        or getattr(user, 'is_super_admin_user', False)
        or getattr(user, 'is_finance_team_user', False)
    )


class FinanceOnlyAdmin(admin.ModelAdmin):
    """Applies the finance-only rule inside Django admin too.

    Admin sits outside the app's own view gate, so registering these models
    without this would mean anyone granted `is_staff` for an unrelated reason
    silently gained every voucher, invoice and partner record.
    """

    def has_module_permission(self, request):
        return can_use_accounting(request.user)

    def has_view_permission(self, request, obj=None):
        return can_use_accounting(request.user)

    def has_add_permission(self, request):
        return can_use_accounting(request.user)

    def has_change_permission(self, request, obj=None):
        return can_use_accounting(request.user)

    def has_delete_permission(self, request, obj=None):
        return can_use_accounting(request.user)


@admin.register(AccountingSettings)
class AccountingSettingsAdmin(FinanceOnlyAdmin):
    """Singleton — the control accounts documents post against."""
    raw_id_fields = ('default_receivable_account', 'default_payable_account',
                     'output_tax_account', 'input_tax_account')

    def has_add_permission(self, request):
        # One row only; edit the existing one rather than creating a second.
        return can_use_accounting(request.user) and not AccountingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Account)
class AccountAdmin(FinanceOnlyAdmin):
    list_display = ('code', 'name', 'internal_type', 'parent', 'is_active')
    list_filter = ('internal_type', 'is_active')
    search_fields = ('code', 'name')
    ordering = ('code',)
    raw_id_fields = ('parent',)
    readonly_fields = ('source_row', 'created_at', 'updated_at')


@admin.register(Partner)
class PartnerAdmin(FinanceOnlyAdmin):
    list_display = ('name', 'kind', 'vat_number', 'is_active')
    list_filter = ('kind', 'is_active')
    search_fields = ('name', 'vat_number', 'cr_number', 'zoho_contact_id')
    raw_id_fields = ('receivable_account', 'payable_account')


class VoucherLineInline(admin.TabularInline):
    model = VoucherLine
    extra = 2
    raw_id_fields = ('account', 'partner', 'project')


@admin.register(Voucher)
class VoucherAdmin(FinanceOnlyAdmin):
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
class DocumentAdmin(FinanceOnlyAdmin):
    list_display = ('number', 'kind', 'partner', 'date', 'due_date', 'total',
                    'amount_paid', 'status')
    list_filter = ('kind', 'status', 'source')
    search_fields = ('number', 'reference', 'partner__name', 'zoho_id')
    date_hierarchy = 'date'
    raw_id_fields = ('partner', 'project')
    inlines = [DocumentLineInline]
