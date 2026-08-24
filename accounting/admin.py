from django.contrib import admin

from .models import (
    Account, AccountingSettings, Document, DocumentLine, Partner, Voucher,
    VoucherLine, ZohoAccountMap, ZohoCredentials,
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


@admin.register(ZohoCredentials)
class ZohoCredentialsAdmin(FinanceOnlyAdmin):
    """Singleton. Secrets are write-only here and never rendered back."""
    readonly_fields = ('status', 'api_domain', 'access_token_expires_at',
                       'last_synced_at', 'updated_at')
    exclude = ('access_token', 'refresh_token')

    def has_add_permission(self, request):
        return can_use_accounting(request.user) and not ZohoCredentials.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Connection status')
    def status(self, obj):
        return obj.status


@admin.register(ZohoAccountMap)
class ZohoAccountMapAdmin(FinanceOnlyAdmin):
    """The worklist: which Zoho account lands in which ERP account."""
    list_display = ('zoho_account_code', 'zoho_account_name', 'zoho_account_type',
                    'account', 'state')
    list_filter = ('zoho_account_type', 'is_ignored', 'zoho_is_active')
    search_fields = ('zoho_account_code', 'zoho_account_name', 'zoho_account_id',
                     'account__code', 'account__name')
    raw_id_fields = ('account',)
    readonly_fields = ('zoho_account_id', 'first_seen_at', 'last_seen_at')
    list_select_related = ('account',)

    @admin.display(description='State')
    def state(self, obj):
        return obj.state

    def has_add_permission(self, request):
        # Rows come from Zoho; adding one by hand would invent an account that
        # does not exist there.
        return False
