import datetime
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

import openpyxl
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounting.models import (
    Account, Document, DocumentLine, Partner, Voucher, VoucherLine, build_tree,
)

# (reporting_code, reporting_name, gl_code, gl_name, internal_type) — the five
# populated columns of the finance workbook, starting at column B.
SAMPLE_ROWS = [
    ('1000000', 'Assets', '1000000', 'Assets', 'View'),
    ('1100000', 'Current Assets', '1100000', 'Current Assets', 'View'),
    ('1110000', 'Cash in hand', '1110000', 'Cash in hand', 'View'),
    (None, None, '1110001', 'Cash in hand', 'Regular'),
    (None, None, '1110002', 'Petty cash', 'Regular'),
    ('1120000', 'Cash at Banks', '1120000', 'Cash at Banks', 'View'),
    (None, None, '1120001', 'ALINMA Bank', 'Liquidity'),
    ('4000000', 'Cost of Sale', '4000000', 'Cost of Sale', 'View'),
    ('4100000', 'Cost of Projects', '4100000', 'Cost of Projects', 'View'),
    (None, None, '4100020', 'Rental Equipment', 'Regular'),
    # 4100025 is the regression guard: zeroing its last digit yields 4100020,
    # which is a real *sibling* leaf above. It must still land under 4100000.
    (None, None, '4100025', 'Material Logistic & Freight', 'Regular'),
]


def write_workbook(rows, path, sheet_name='changing'):
    """Build a workbook shaped like the finance file: title row, header row,
    then data from row 3 in columns B–F."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.cell(1, 1, 'Chart of Accounts')
    for col, label in enumerate(['Reporting code #', 'Balance Sheet Description',
                                 'G/L code #', 'G/L Description', 'Internal Type'], start=2):
        ws.cell(2, col, label)
    for i, row in enumerate(rows, start=3):
        for col, value in enumerate(row, start=2):
            if value is not None:
                ws.cell(i, col, value)
    wb.save(path)
    return path


class ChartImportTests(TestCase):
    """The import_chart_of_accounts command."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = write_workbook(SAMPLE_ROWS, str(Path(self.dir) / 'coa.xlsx'))

    def _import(self, *args):
        out = StringIO()
        call_command('import_chart_of_accounts', self.path, *args, stdout=out)
        return out.getvalue()

    def test_imports_every_account(self):
        self._import()
        self.assertEqual(Account.objects.count(), len(SAMPLE_ROWS))
        bank = Account.objects.get(code='1120001')
        self.assertEqual(bank.name, 'ALINMA Bank')
        self.assertEqual(bank.internal_type, Account.TYPE_LIQUIDITY)

    def test_rerun_updates_rather_than_duplicating(self):
        self._import()
        rows = [list(r) for r in SAMPLE_ROWS]
        rows[4][3] = 'Petty cash (renamed)'
        write_workbook(rows, self.path)
        output = self._import()
        self.assertEqual(Account.objects.count(), len(SAMPLE_ROWS))
        self.assertEqual(Account.objects.get(code='1110002').name, 'Petty cash (renamed)')
        self.assertIn('0 created', output)

    def test_parent_is_nearest_heading_not_a_sibling_leaf(self):
        """Regression: 4100025 must not be filed under the leaf 4100020."""
        self._import()
        self.assertEqual(Account.objects.get(code='4100025').parent.code, '4100000')
        self.assertEqual(Account.objects.get(code='4100020').parent.code, '4100000')

    def test_parent_chain_and_roots(self):
        self._import()
        self.assertEqual(Account.objects.get(code='1110001').parent.code, '1110000')
        self.assertEqual(Account.objects.get(code='1110000').parent.code, '1100000')
        self.assertIsNone(Account.objects.get(code='1000000').parent)
        roots = set(Account.objects.filter(parent__isnull=True).values_list('code', flat=True))
        self.assertEqual(roots, {'1000000', '4000000'})

    def test_no_regular_account_ever_parents_another(self):
        self._import()
        for account in Account.objects.exclude(internal_type=Account.TYPE_VIEW):
            self.assertFalse(account.children.exists(), f'{account.code} has children')

    def test_duplicate_code_keeps_first_and_warns(self):
        rows = SAMPLE_ROWS + [(None, None, '1110001', 'Impostor', 'Regular')]
        write_workbook(rows, self.path)
        output = self._import()
        self.assertIn('duplicate code 1110001', output)
        self.assertEqual(Account.objects.get(code='1110001').name, 'Cash in hand')

    def test_unknown_internal_type_falls_back_to_regular(self):
        rows = SAMPLE_ROWS + [(None, None, '1110003', 'Odd one', 'Nonsense')]
        write_workbook(rows, self.path)
        output = self._import()
        self.assertIn('unrecognised Internal Type', output)
        self.assertEqual(Account.objects.get(code='1110003').internal_type,
                         Account.TYPE_REGULAR)

    def test_dry_run_writes_nothing(self):
        output = self._import('--dry-run')
        self.assertIn('Dry run', output)
        self.assertEqual(Account.objects.count(), 0)

    def test_deactivate_missing_keeps_the_row(self):
        self._import()
        stale = Account.objects.create(code='9999999', name='Gone', internal_type='Regular')
        self._import('--deactivate-missing')
        stale.refresh_from_db()
        self.assertFalse(stale.is_active)
        self.assertTrue(Account.objects.get(code='1110001').is_active)

    def test_missing_file_raises(self):
        with self.assertRaises(CommandError):
            call_command('import_chart_of_accounts', str(Path(self.dir) / 'nope.xlsx'),
                         stdout=StringIO())

    def test_unknown_sheet_raises(self):
        with self.assertRaises(CommandError):
            call_command('import_chart_of_accounts', self.path, '--sheet', 'missing',
                         stdout=StringIO())


class AccountModelTests(TestCase):

    def test_is_postable_excludes_only_view(self):
        view = Account.objects.create(code='1000000', name='Assets', internal_type='View')
        leaf = Account.objects.create(code='1110001', name='Cash', internal_type='Regular',
                                      parent=view)
        self.assertFalse(view.is_postable)
        self.assertTrue(leaf.is_postable)
        self.assertEqual(list(Account.objects.postable()), [leaf])
        self.assertEqual(list(Account.objects.headings()), [view])

    def test_account_class_from_leading_digit(self):
        self.assertEqual(
            Account(code='4100001', name='x').account_class, 'Cost of Sale')
        self.assertEqual(Account(code='9100001', name='x').account_class, '')

    def test_build_tree_depths(self):
        a = Account.objects.create(code='1000000', name='Assets', internal_type='View')
        b = Account.objects.create(code='1100000', name='Current', internal_type='View', parent=a)
        c = Account.objects.create(code='1110001', name='Cash', internal_type='Regular', parent=b)
        depths = {x.code: x.tree_depth for x in build_tree(Account.objects.all())}
        self.assertEqual(depths, {'1000000': 0, '1100000': 1, '1110001': 2})

    def test_build_tree_treats_filtered_out_parents_as_roots(self):
        """A searched/filtered list must not render orphaned indentation."""
        a = Account.objects.create(code='1000000', name='Assets', internal_type='View')
        c = Account.objects.create(code='1110001', name='Cash', internal_type='Regular', parent=a)
        rows = build_tree(Account.objects.filter(pk=c.pk))
        self.assertEqual(rows[0].tree_depth, 0)


class ChartViewTests(TestCase):

    def setUp(self):
        self.view = Account.objects.create(code='1000000', name='Assets', internal_type='View')
        self.cash = Account.objects.create(code='1110001', name='Petty cash',
                                           internal_type='Regular', parent=self.view)
        self.bank = Account.objects.create(code='1120001', name='ALINMA Bank',
                                           internal_type='Liquidity', parent=self.view)

    def _user(self, role_name, username):
        role, _ = Role.objects.get_or_create(name=role_name)
        user = User.objects.create_user(username, password='x')
        user.role = role
        user.save()
        return user

    def test_finance_and_super_admin_can_open(self):
        for role_name, username in [(Role.SUPER_ADMIN, 'sa'), (Role.FINANCE_HEAD, 'fh'),
                                    (Role.FINANCE_MANAGER, 'fm'), (Role.FINANCE_REP, 'fr')]:
            self.client.force_login(self._user(role_name, username))
            self.assertEqual(self.client.get(reverse('accounting:chart')).status_code, 200,
                             f'{role_name} should be allowed')

    def test_other_roles_are_denied(self):
        self.client.force_login(self._user(Role.SALES_REP, 'rep'))
        self.assertEqual(self.client.get(reverse('accounting:chart')).status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        self.assertEqual(self.client.get(reverse('accounting:chart')).status_code, 302)

    def test_lists_accounts_with_totals(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        resp = self.client.get(reverse('accounting:chart'))
        self.assertContains(resp, 'ALINMA Bank')
        self.assertEqual(resp.context['total_count'], 3)
        self.assertEqual(resp.context['postable_count'], 2)
        self.assertEqual(resp.context['heading_count'], 1)

    def test_search_filters_by_code_and_name(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        resp = self.client.get(reverse('accounting:chart'), {'q': 'ALINMA'})
        self.assertEqual([a.code for a in resp.context['rows']], ['1120001'])
        resp = self.client.get(reverse('accounting:chart'), {'q': '1110001'})
        self.assertEqual([a.code for a in resp.context['rows']], ['1110001'])

    def test_type_and_postable_filters(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        resp = self.client.get(reverse('accounting:chart'), {'type': 'Liquidity'})
        self.assertEqual([a.code for a in resp.context['rows']], ['1120001'])
        resp = self.client.get(reverse('accounting:chart'), {'postable': '0'})
        self.assertEqual([a.code for a in resp.context['rows']], ['1000000'])

    def test_inactive_hidden_unless_requested(self):
        self.bank.is_active = False
        self.bank.save()
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        resp = self.client.get(reverse('accounting:chart'))
        self.assertNotIn('1120001', [a.code for a in resp.context['rows']])
        resp = self.client.get(reverse('accounting:chart'), {'inactive': '1'})
        self.assertIn('1120001', [a.code for a in resp.context['rows']])

    def test_empty_state_when_nothing_imported(self):
        # Children first: parent is PROTECTed, so a heading cannot be deleted
        # out from under its accounts.
        Account.objects.filter(parent__isnull=False).delete()
        Account.objects.all().delete()
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        resp = self.client.get(reverse('accounting:chart'))
        self.assertContains(resp, 'import_chart_of_accounts')

    def test_heading_with_children_cannot_be_deleted(self):
        from django.db.models.deletion import ProtectedError
        with self.assertRaises(ProtectedError):
            self.view.delete()

    def test_rows_carry_subtree_counts(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        resp = self.client.get(reverse('accounting:chart'))
        by_code = {a.code: a.counts for a in resp.context['rows']}
        self.assertEqual(by_code['1000000']['direct'], 2)
        self.assertEqual(by_code['1000000']['postable'], 2)
        self.assertEqual(by_code['1110001']['direct'], 0)

    def test_counts_ignore_the_active_filter(self):
        """A heading reports its true size even while the list is filtered."""
        self.client.force_login(self._user(Role.SUPER_ADMIN, 'sa'))
        resp = self.client.get(reverse('accounting:chart'), {'type': 'View'})
        counts = {a.code: a.counts for a in resp.context['rows']}
        self.assertEqual(counts['1000000']['direct'], 2)


class AccountDetailTests(TestCase):
    """Drill-down: a heading lists what is filed beneath it."""

    def setUp(self):
        self.assets = Account.objects.create(code='1000000', name='Assets', internal_type='View')
        self.current = Account.objects.create(code='1100000', name='Current Assets',
                                              internal_type='View', parent=self.assets)
        self.banks = Account.objects.create(code='1120000', name='Cash at Banks',
                                            internal_type='View', parent=self.current)
        self.alinma = Account.objects.create(code='1120001', name='ALINMA Bank',
                                             internal_type='Liquidity', parent=self.banks)
        self.sab = Account.objects.create(code='1120002', name='SAB SAR BANK',
                                          internal_type='Liquidity', parent=self.banks)
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('sa', password='x')
        self.user.role = role
        self.user.save()
        self.client.force_login(self.user)

    def _get(self, code):
        return self.client.get(reverse('accounting:account_detail', args=[code]))

    def test_heading_lists_its_children(self):
        resp = self._get('1120000')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([c.code for c in resp.context['children']], ['1120001', '1120002'])
        self.assertContains(resp, 'ALINMA Bank')

    def test_breadcrumb_runs_outermost_first(self):
        resp = self._get('1120001')
        self.assertEqual([a.code for a in resp.context['ancestors']],
                         ['1000000', '1100000', '1120000'])

    def test_leaf_shows_siblings_instead(self):
        resp = self._get('1120001')
        self.assertEqual(resp.context['children'], [])
        self.assertEqual([s.code for s in resp.context['siblings']], ['1120002'])

    def test_counts_cover_the_whole_subtree(self):
        resp = self._get('1000000')
        self.assertEqual(resp.context['counts'],
                         {'direct': 1, 'total': 4, 'postable': 2})

    def test_top_level_has_no_parent_or_siblings(self):
        resp = self._get('1000000')
        self.assertIsNone(resp.context['account'].parent)
        self.assertEqual(resp.context['siblings'], [])

    def test_unknown_code_404s(self):
        self.assertEqual(self._get('9999999').status_code, 404)

    def test_requires_finance_or_super_admin(self):
        role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        rep = User.objects.create_user('rep', password='x')
        rep.role = role
        rep.save()
        self.client.force_login(rep)
        self.assertEqual(self._get('1000000').status_code, 403)


class VoucherTests(TestCase):
    """JV / PV / RV — the double-entry records."""

    def setUp(self):
        self.assets = Account.objects.create(code='1000000', name='Assets', internal_type='View')
        self.bank = Account.objects.create(code='1120001', name='ALINMA Bank',
                                           internal_type='Liquidity', parent=self.assets)
        self.expense = Account.objects.create(code='4100006', name='Local Procurement',
                                              internal_type='Regular', parent=self.assets)
        self.partner = Partner.objects.create(name='Al Ghad Al Taqni Est',
                                              kind=Partner.KIND_VENDOR)

    def _voucher(self, vtype=Voucher.TYPE_PV, number='PV-0001'):
        return Voucher.objects.create(voucher_type=vtype, number=number,
                                      date=datetime.date(2026, 8, 1))

    def _balance(self, voucher, amount='1000.00'):
        VoucherLine.objects.create(voucher=voucher, account=self.expense,
                                   debit=Decimal(amount), order=0)
        VoucherLine.objects.create(voucher=voucher, account=self.bank,
                                   credit=Decimal(amount), order=1)

    def test_balanced_voucher_posts(self):
        v = self._voucher()
        self._balance(v)
        self.assertTrue(v.is_balanced)
        v.post()
        v.refresh_from_db()
        self.assertEqual(v.status, Voucher.STATUS_POSTED)

    def test_unbalanced_voucher_refuses_to_post(self):
        v = self._voucher()
        VoucherLine.objects.create(voucher=v, account=self.expense, debit=Decimal('1000'))
        VoucherLine.objects.create(voucher=v, account=self.bank, credit=Decimal('900'))
        self.assertFalse(v.is_balanced)
        with self.assertRaises(ValidationError):
            v.post()
        v.refresh_from_db()
        self.assertEqual(v.status, Voucher.STATUS_DRAFT)

    def test_empty_voucher_is_not_balanced_at_zero(self):
        """A voucher with no lines must not slip through as 'balances at 0'."""
        with self.assertRaises(ValidationError):
            self._voucher().post()

    def test_posting_twice_is_refused(self):
        v = self._voucher()
        self._balance(v)
        v.post()
        with self.assertRaises(ValidationError):
            v.post()

    def test_line_cannot_be_both_debit_and_credit(self):
        line = VoucherLine(voucher=self._voucher(), account=self.bank,
                           debit=Decimal('10'), credit=Decimal('10'))
        with self.assertRaises(ValidationError):
            line.clean()

    def test_line_needs_an_amount(self):
        with self.assertRaises(ValidationError):
            VoucherLine(voucher=self._voucher(), account=self.bank).clean()

    def test_line_rejects_negative_amounts(self):
        with self.assertRaises(ValidationError):
            VoucherLine(voucher=self._voucher(), account=self.bank,
                        debit=Decimal('-5')).clean()

    def test_line_cannot_post_to_a_heading(self):
        """View accounts group other accounts and must never carry a posting."""
        line = VoucherLine(voucher=self._voucher(), account=self.assets,
                           debit=Decimal('10'))
        with self.assertRaises(ValidationError):
            line.clean()

    def test_totals_and_headline_amount(self):
        v = self._voucher()
        self._balance(v, '2500.50')
        self.assertEqual(v.total_debit, Decimal('2500.50'))
        self.assertEqual(v.total_credit, Decimal('2500.50'))
        self.assertEqual(v.amount, Decimal('2500.50'))

    def test_number_unique_within_a_type_but_not_across(self):
        self._voucher(Voucher.TYPE_JV, '0001')
        self._voucher(Voucher.TYPE_RV, '0001')      # different type — fine
        with self.assertRaises(IntegrityError):
            self._voucher(Voucher.TYPE_JV, '0001')

    def test_all_three_voucher_types_exist(self):
        self.assertEqual({c[0] for c in Voucher.TYPE_CHOICES}, {'JV', 'PV', 'RV'})


class DocumentTests(TestCase):
    """Customer invoices and vendor bills — one model, two directions."""

    def setUp(self):
        root = Account.objects.create(code='3000000', name='Revenues', internal_type='View')
        self.income = Account.objects.create(code='3000101', name='Sales - Projects',
                                             internal_type='Regular', parent=root)
        self.customer = Partner.objects.create(name='Saudi Aramco', kind=Partner.KIND_CUSTOMER)
        self.vendor = Partner.objects.create(name='PT Neo Energy', kind=Partner.KIND_VENDOR)
        self.inv = Document.objects.create(
            kind=Document.KIND_INVOICE, number='INV-001', partner=self.customer,
            date=datetime.date(2026, 8, 1), due_date=datetime.date(2026, 9, 1),
            subtotal=Decimal('100000'), tax_total=Decimal('15000'),
            total=Decimal('115000'), status=Document.STATUS_OPEN)

    def test_invoices_and_bills_split_by_kind(self):
        Document.objects.create(kind=Document.KIND_BILL, number='BILL-001',
                                partner=self.vendor, date=datetime.date(2026, 8, 2))
        self.assertEqual([d.number for d in Document.objects.invoices()], ['INV-001'])
        self.assertEqual([d.number for d in Document.objects.bills()], ['BILL-001'])

    def test_balance_due_tracks_payment(self):
        self.assertEqual(self.inv.balance_due, Decimal('115000'))
        self.inv.amount_paid = Decimal('40000')
        self.assertEqual(self.inv.balance_due, Decimal('75000'))

    def test_overdue_only_while_money_is_outstanding(self):
        self.inv.due_date = datetime.date(2020, 1, 1)
        self.assertTrue(self.inv.is_overdue)
        self.inv.amount_paid = Decimal('115000')
        self.inv.status = Document.STATUS_PAID
        self.assertFalse(self.inv.is_overdue)

    def test_not_overdue_without_a_due_date(self):
        self.inv.due_date = None
        self.assertFalse(self.inv.is_overdue)

    def test_lines_total_surfaces_an_import_that_does_not_add_up(self):
        DocumentLine.objects.create(document=self.inv, description='Phase 1',
                                    account=self.income, amount=Decimal('60000'))
        DocumentLine.objects.create(document=self.inv, description='Phase 2',
                                    account=self.income, amount=Decimal('30000'))
        # Stored subtotal is 100,000 but the lines only reach 90,000. The gap
        # must stay visible rather than being silently reconciled away.
        self.assertEqual(self.inv.lines_total, Decimal('90000'))
        self.assertNotEqual(self.inv.lines_total, self.inv.subtotal)

    def test_document_line_cannot_post_to_a_heading(self):
        heading = Account.objects.get(code='3000000')
        with self.assertRaises(ValidationError):
            DocumentLine(document=self.inv, account=heading).clean()

    def test_number_unique_within_a_kind_but_not_across(self):
        Document.objects.create(kind=Document.KIND_BILL, number='INV-001',
                                partner=self.vendor, date=datetime.date(2026, 8, 3))
        with self.assertRaises(IntegrityError):
            Document.objects.create(kind=Document.KIND_INVOICE, number='INV-001',
                                    partner=self.customer, date=datetime.date(2026, 8, 4))

    def test_outstanding_excludes_paid_and_void(self):
        Document.objects.create(kind=Document.KIND_BILL, number='B-PAID',
                                partner=self.vendor, date=datetime.date(2026, 8, 5),
                                status=Document.STATUS_PAID)
        Document.objects.create(kind=Document.KIND_BILL, number='B-VOID',
                                partner=self.vendor, date=datetime.date(2026, 8, 6),
                                status=Document.STATUS_VOID)
        self.assertEqual([d.number for d in Document.objects.outstanding()], ['INV-001'])


class PartnerTests(TestCase):

    def test_kind_flags(self):
        c = Partner.objects.create(name='C', kind=Partner.KIND_CUSTOMER)
        v = Partner.objects.create(name='V', kind=Partner.KIND_VENDOR)
        b = Partner.objects.create(name='B', kind=Partner.KIND_BOTH)
        self.assertEqual((c.is_customer, c.is_vendor), (True, False))
        self.assertEqual((v.is_customer, v.is_vendor), (False, True))
        self.assertEqual((b.is_customer, b.is_vendor), (True, True))
