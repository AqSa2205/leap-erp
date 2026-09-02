"""Publishing a chart-of-accounts revision.

The chart is the structure every ledger entry is coded against, and finance
revises it — this is already the second revision. So the tests care about two
things above all: that a revision never destroys what came before, and that
what the preview promises is what the apply does.
"""
import io
import time
from unittest import mock

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounting.chart_import import (
    ChartImportError, apply, ancestor_codes, parse_rows, plan, read_grid,
    sniff_format,
)
from accounting.models import Account

User = get_user_model()

HEADER = ['', 'Reporting code #', 'Balance Sheet Description',
          'G/L code #', 'G/L Description', 'Internal Type']


def workbook_bytes(rows, sheet_name='changing'):
    """Build a workbook in the finance layout: title row, header, then data."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = sheet_name
    sheet.append(['Chart of Accounts', '', '', '', '', ''])
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def account_row(code, name, internal_type='Regular'):
    return ['', '', '', code, name, internal_type]


class FormatSniffingTests(TestCase):
    """An extension is a claim; the first bytes are a fact.

    The revision that prompted this work was a genuine legacy .xls named .xls,
    but finance's tooling relabels these in both directions and openpyxl cannot
    open an OLE2 file.
    """

    def test_an_xlsx_is_recognised(self):
        self.assertEqual(sniff_format(b'PK\x03\x04rest'), 'xlsx')

    def test_a_legacy_xls_is_recognised(self):
        self.assertEqual(sniff_format(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'), 'xls')

    def test_anything_else_is_not_a_workbook(self):
        self.assertIsNone(sniff_format(b'code,name\n1,2'))

    def test_a_non_workbook_is_refused_with_something_actionable(self):
        with self.assertRaises(ChartImportError) as ctx:
            read_grid(io.BytesIO(b'not a workbook at all'))
        self.assertIn('Excel', str(ctx.exception))

    def test_an_xlsx_named_xls_still_reads(self):
        """The failure this guards against is the one that actually happens."""
        payload = workbook_bytes([account_row('1000000', 'Assets', 'View')])
        _sheet, rows = read_grid(io.BytesIO(payload))
        parsed, _dupes, _bad = parse_rows(rows)
        self.assertEqual([item['code'] for item in parsed], ['1000000'])


class ParsingTests(TestCase):

    def _parse(self, rows):
        _sheet, grid = read_grid(io.BytesIO(workbook_bytes(rows)))
        return parse_rows(grid)

    def test_a_numeric_code_is_not_left_as_a_float(self):
        """xlrd reads every numeric cell as a float, so a real .xls hands over
        1110001.0 — which is not a code, and would be skipped as non-numeric.

        Fed to parse_rows directly rather than through a generated workbook:
        openpyxl round-trips an int as an int, so a written fixture never
        produces the value this guards against and the test would pass whether
        or not the stripping existed.
        """
        grid = [['Chart of Accounts'], HEADER,
                ['', '', '', 1110001.0, 'Cash in hand', 'Regular']]
        parsed, _dupes, _bad = parse_rows(grid)
        self.assertEqual([item['code'] for item in parsed], ['1110001'])

    def test_a_heading_row_uses_its_gl_pair(self):
        parsed, _dupes, _bad = self._parse(
            [['', '1000000', 'Assets', '1000000', 'Assets', 'View']])
        self.assertEqual(parsed[0]['code'], '1000000')
        self.assertEqual(parsed[0]['internal_type'], 'View')

    def test_a_duplicate_code_is_reported_and_skipped(self):
        parsed, duplicates, _bad = self._parse([
            account_row('1110001', 'Cash'),
            account_row('1110001', 'Cash again'),
        ])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(duplicates[0][1], '1110001')

    def test_an_unrecognised_type_falls_back_to_regular_and_is_reported(self):
        parsed, _dupes, bad = self._parse([account_row('1110001', 'Cash', 'Widget')])
        self.assertEqual(parsed[0]['internal_type'], 'Regular')
        self.assertEqual(bad[0][2], 'Widget')

    def test_non_numeric_and_blank_rows_are_skipped(self):
        parsed, _dupes, _bad = self._parse([
            account_row('TOTAL', 'Total row'),
            ['', '', '', '', '', ''],
            account_row('1110001', 'Cash'),
        ])
        self.assertEqual([item['code'] for item in parsed], ['1110001'])

    def test_whitespace_in_a_name_is_collapsed(self):
        parsed, _dupes, _bad = self._parse(
            [account_row('1110001', '  Cash   in \n hand ')])
        self.assertEqual(parsed[0]['name'], 'Cash in hand')


class ParentLinkingTests(TestCase):

    def test_the_nearest_ancestor_is_tried_first(self):
        self.assertEqual(next(iter(ancestor_codes('1110001'))), '1110000')

    def test_a_missing_intermediate_heading_does_not_orphan_an_account(self):
        """A chart with a gap should still link up rather than sprout roots."""
        Account.objects.create(code='1000000', name='Assets', internal_type='View')
        payload = workbook_bytes([account_row('1110001', 'Cash in hand')])
        _sheet, rows = read_grid(io.BytesIO(payload))
        parsed, _dupes, _bad = parse_rows(rows)
        apply(parsed)
        self.assertEqual(Account.objects.get(code='1110001').parent.code, '1000000')

    def test_a_leaf_never_parents_another_account(self):
        """Zeroing a digit of 4100025 gives 4100020, which in this chart is a
        real leaf (Rental Equipment), not a heading. Only a View may parent —
        that is what the type means. Filing an account under its sibling
        corrupts every subtree total that reads down the tree.
        """
        Account.objects.create(code='4000000', name='Cost of Sale',
                               internal_type='View')
        Account.objects.create(code='4100000', name='Direct Costs',
                               internal_type='View')
        Account.objects.create(code='4100020', name='Rental Equipment',
                               internal_type='Regular')
        payload = workbook_bytes([account_row('4100025', 'Site Vehicles')])
        _sheet, rows = read_grid(io.BytesIO(payload))
        parsed, _dupes, _bad = parse_rows(rows)
        apply(parsed)
        self.assertEqual(Account.objects.get(code='4100025').parent.code, '4100000')

    def test_ordering_in_the_sheet_does_not_matter(self):
        """Children before parents is a normal way for a workbook to be edited."""
        payload = workbook_bytes([
            account_row('1110001', 'Cash in hand'),
            account_row('1110000', 'Cash', 'View'),
            account_row('1000000', 'Assets', 'View'),
        ])
        _sheet, rows = read_grid(io.BytesIO(payload))
        parsed, _dupes, _bad = parse_rows(rows)
        apply(parsed)
        self.assertEqual(Account.objects.get(code='1110001').parent.code, '1110000')


class PlanTests(TestCase):
    """The preview has to describe the apply, or it is worse than nothing."""

    def setUp(self):
        Account.objects.create(code='1000000', name='Assets', internal_type='View')
        Account.objects.create(code='1110001', name='Cash in hand',
                               internal_type='Regular')
        Account.objects.create(code='1110002', name='Petty cash',
                               internal_type='Regular')

    def _plan(self, rows):
        _sheet, grid = read_grid(io.BytesIO(workbook_bytes(rows)))
        parsed, _dupes, _bad = parse_rows(grid)
        return parsed, plan(parsed)

    def test_a_new_code_is_reported_as_new(self):
        _parsed, proposed = self._plan([account_row('1110003', 'Cash at bank')])
        self.assertEqual([i['code'] for i in proposed['created']], ['1110003'])

    def test_a_renamed_account_is_reported_with_both_names(self):
        _parsed, proposed = self._plan([account_row('1110001', 'Cash on hand')])
        change = proposed['updated'][0]
        self.assertEqual(change['from_name'], 'Cash in hand')
        self.assertEqual(change['to_name'], 'Cash on hand')

    def test_a_type_change_is_reported(self):
        """View to Regular turns a heading into something postable — it changes
        where money can land, so it must not slip past unread."""
        _parsed, proposed = self._plan([account_row('1000000', 'Assets', 'Regular')])
        change = proposed['updated'][0]
        self.assertEqual((change['from_type'], change['to_type']), ('View', 'Regular'))

    def test_an_identical_row_is_reported_as_unchanged(self):
        _parsed, proposed = self._plan([account_row('1110001', 'Cash in hand')])
        self.assertEqual([i['code'] for i in proposed['unchanged']], ['1110001'])
        self.assertEqual(proposed['updated'], [])

    def test_accounts_absent_from_the_file_are_listed(self):
        _parsed, proposed = self._plan([account_row('1110001', 'Cash in hand')])
        self.assertIn('1110002', [a.code for a in proposed['missing']])

    def test_the_plan_matches_what_apply_actually_does(self):
        """The property that makes a preview worth having."""
        parsed, proposed = self._plan([
            account_row('1110001', 'Cash on hand'),
            account_row('1110003', 'Cash at bank'),
        ])
        before = Account.objects.count()
        result = apply(parsed)
        self.assertEqual(result['created'], len(proposed['created']))
        self.assertEqual(Account.objects.count(), before + len(proposed['created']))
        self.assertEqual(Account.objects.get(code='1110001').name, 'Cash on hand')


class ApplyTests(TestCase):

    def setUp(self):
        Account.objects.create(code='1000000', name='Assets', internal_type='View')
        self.retired = Account.objects.create(code='1110002', name='Petty cash',
                                              internal_type='Regular')

    def _apply(self, rows, **kwargs):
        _sheet, grid = read_grid(io.BytesIO(workbook_bytes(rows)))
        parsed, _dupes, _bad = parse_rows(grid)
        return apply(parsed, **kwargs)

    def test_reimporting_updates_rather_than_duplicating(self):
        self._apply([account_row('1110001', 'Cash in hand')])
        self._apply([account_row('1110001', 'Cash in hand')])
        self.assertEqual(Account.objects.filter(code='1110001').count(), 1)

    def test_an_absent_account_is_kept_by_default(self):
        """Nothing is ever deleted — a chart of accounts carries history."""
        self._apply([account_row('1110001', 'Cash in hand')])
        self.retired.refresh_from_db()
        self.assertTrue(self.retired.is_active)

    def test_an_absent_account_can_be_deactivated_but_never_deleted(self):
        result = self._apply([account_row('1110001', 'Cash in hand')],
                             deactivate_missing=True)
        self.retired.refresh_from_db()
        self.assertFalse(self.retired.is_active)
        self.assertGreaterEqual(result['deactivated'], 1)
        self.assertTrue(Account.objects.filter(code='1110002').exists())

    def test_a_returning_account_is_reactivated(self):
        self.retired.is_active = False
        self.retired.save()
        self._apply([account_row('1110002', 'Petty cash')])
        self.retired.refresh_from_db()
        self.assertTrue(self.retired.is_active)


class ChartImportScreenTests(TestCase):

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.finance = User.objects.create_user(
            'ci-super', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.outsider = User.objects.create_user(
            'ci-out', password='x',
            role=Role.objects.get(name=Role.DOCUMENT_CONTROLLER))

        Account.objects.create(code='1000000', name='Assets', internal_type='View')
        Account.objects.create(code='1110001', name='Cash in hand',
                               internal_type='Regular')

        self.url = reverse('accounting:chart_import')
        self.apply_url = reverse('accounting:chart_import_apply')

    def _upload(self, rows=None, name='chart.xlsx'):
        payload = workbook_bytes(rows if rows is not None else
                                 [account_row('1110003', 'Cash at bank')])
        return SimpleUploadedFile(name, payload,
                                  content_type='application/vnd.ms-excel')

    def _preview(self, rows=None):
        """Preview, and hand back the signed payload a browser would carry."""
        response = self.client.post(self.url, {'workbook': self._upload(rows)})
        return response.context['payload']

    # ── access ──────────────────────────────────────────────────────────────

    def test_someone_outside_finance_cannot_open_it(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_someone_outside_finance_cannot_apply(self):
        self.client.force_login(self.outsider)
        self.client.post(self.apply_url)
        self.assertFalse(Account.objects.filter(code='1110003').exists())

    def test_applying_refuses_a_get(self):
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(self.apply_url).status_code, 405)

    # ── preview ─────────────────────────────────────────────────────────────

    def test_uploading_previews_without_changing_anything(self):
        """The whole point of the two steps."""
        self.client.force_login(self.finance)
        response = self.client.post(self.url, {'workbook': self._upload()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([i['code'] for i in response.context['plan']['created']],
                         ['1110003'])
        self.assertFalse(Account.objects.filter(code='1110003').exists())

    def test_a_file_that_is_not_a_workbook_is_refused(self):
        self.client.force_login(self.finance)
        bad = SimpleUploadedFile('notes.txt', b'this is not a workbook',
                                 content_type='text/plain')
        response = self.client.post(self.url, {'workbook': bad}, follow=True)
        self.assertContains(response, 'not an Excel workbook')

    def test_a_workbook_with_no_account_rows_is_refused(self):
        self.client.force_login(self.finance)
        response = self.client.post(self.url, {'workbook': self._upload(rows=[])},
                                    follow=True)
        self.assertContains(response, 'No account rows found')

    # ── apply ───────────────────────────────────────────────────────────────

    def test_applying_after_a_preview_writes_the_changes(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'payload': self._preview()})
        self.assertTrue(Account.objects.filter(code='1110003').exists())

    def test_applying_with_no_payload_does_nothing(self):
        """A stray POST — a refresh, a stale tab — must not import anything."""
        self.client.force_login(self.finance)
        response = self.client.post(self.apply_url, follow=True)
        self.assertContains(response, 'upload a workbook first')

    def test_applying_uses_what_was_previewed_not_what_is_posted_beside_it(self):
        """The apply runs the signed rows, so a file attached to the confirm
        step is ignored rather than quietly replacing the previewed one."""
        self.client.force_login(self.finance)
        payload = self._preview()
        self.client.post(self.apply_url, {
            'payload': payload,
            'workbook': self._upload(rows=[account_row('9999999', 'Sneaky account')]),
        })
        self.assertTrue(Account.objects.filter(code='1110003').exists())
        self.assertFalse(Account.objects.filter(code='9999999').exists())

    def test_a_tampered_payload_is_refused(self):
        """The rows come back from the browser, so they are trustworthy only
        because they are signed."""
        self.client.force_login(self.finance)
        payload = self._preview()
        response = self.client.post(
            self.apply_url, {'payload': payload[:-4] + 'AAAA'}, follow=True)
        self.assertContains(response, 'could not be verified')
        self.assertFalse(Account.objects.filter(code='1110003').exists())

    def test_a_stale_preview_is_refused(self):
        """An hour on, the chart may have moved and the diff no longer
        describes it."""
        self.client.force_login(self.finance)
        payload = self._preview()
        with mock.patch('django.core.signing.time.time',
                        return_value=time.time() + 7200):
            response = self.client.post(self.apply_url, {'payload': payload},
                                        follow=True)
        self.assertContains(response, 'more than an hour old')
        self.assertFalse(Account.objects.filter(code='1110003').exists())

    def test_deactivation_happens_only_when_asked(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'payload': self._preview()})
        self.assertTrue(Account.objects.get(code='1110001').is_active)

    def test_deactivation_when_asked_never_deletes(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'payload': self._preview(),
                                          'deactivate_missing': 'on'})
        self.assertFalse(Account.objects.get(code='1110001').is_active)
        self.assertTrue(Account.objects.filter(code='1110001').exists())

    def test_applying_the_same_preview_twice_is_harmless(self):
        """Upserts by code, so a double submit converges rather than
        duplicating — worth pinning, since a signed payload is replayable."""
        self.client.force_login(self.finance)
        payload = self._preview()
        self.client.post(self.apply_url, {'payload': payload})
        self.client.post(self.apply_url, {'payload': payload})
        self.assertEqual(Account.objects.filter(code='1110003').count(), 1)
