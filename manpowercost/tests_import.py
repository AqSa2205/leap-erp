"""Importer tests.

Every case here is a defect in the importer this replaces: it created the
sheet before parsing, ran without a transaction, and turned any column it
could not map - including Gross Salary - into a silent zero.
"""

from decimal import Decimal
from io import BytesIO

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from manpowercost.models import CostBasis, ManpowerCostSheet, ManpowerCostLine


def workbook(rows, headers=None):
    """Build an .xlsx upload in memory."""
    headers = headers or ['Sr. No.', 'Employees Name', 'Department',
                          'Designation', 'Gross Salary', 'Iqama Cost']
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Costing'
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(
        'sheet.xlsx', buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


class ImportPreviewTests(TestCase):

    def setUp(self):
        CostBasis.objects.create(name='B', is_default=True)
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('imp', password='x', role=role)
        self.client.force_login(self.user)
        self.url = reverse('manpowercost:sheet_import')

    def test_preview_writes_nothing(self):
        """The old importer created the sheet before it had parsed a row."""
        resp = self.client.post(self.url, {
            'workbook': workbook([[1, 'Ali', 'PMT', 'Engineer', 12000, 850]])})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ManpowerCostSheet.objects.count(), 0)
        self.assertEqual(ManpowerCostLine.objects.count(), 0)

    def test_preview_lists_the_rows_it_would_create(self):
        resp = self.client.post(self.url, {
            'workbook': workbook([
                [1, 'Ali', 'PMT', 'Engineer', 12000, 850],
                [2, 'Sara', 'Admin', 'GRO', 6600, 0]])})
        self.assertEqual(len(resp.context['rows']), 2)
        self.assertEqual(resp.context['rows'][0]['employee_name'], 'Ali')

    def test_a_column_that_does_not_map_is_reported(self):
        """Silently ignoring it is how a cost quietly goes missing."""
        resp = self.client.post(self.url, {
            'workbook': workbook(
                [[1, 'Ali', 'PMT', 'Engineer', 12000, 850, 999]],
                headers=['Sr. No.', 'Employees Name', 'Department',
                         'Designation', 'Gross Salary', 'Iqama Cost',
                         'Housing Allowance'])})
        self.assertIn('housing allowance', resp.context['unmapped'])

    def test_a_row_with_no_salary_is_called_out_before_saving(self):
        """The defect that made both source workbooks understate cost."""
        resp = self.client.post(self.url, {
            'workbook': workbook([
                [1, 'Ali', 'PMT', 'Engineer', 12000, 850],
                [2, 'Ghost', 'PMT', 'Engineer', 0, 850]])})
        self.assertEqual(len(resp.context['zero_salary']), 1)
        self.assertEqual(resp.context['zero_salary'][0]['employee_name'], 'Ghost')

    def test_rows_without_a_name_or_designation_are_skipped(self):
        resp = self.client.post(self.url, {
            'workbook': workbook([
                [1, 'Ali', 'PMT', 'Engineer', 12000, 850],
                [2, None, None, None, None, None]])})
        self.assertEqual(len(resp.context['rows']), 1)

    def test_a_file_that_is_not_a_workbook_is_refused_cleanly(self):
        bad = SimpleUploadedFile('x.xlsx', b'not a workbook',
                                 content_type='application/octet-stream')
        resp = self.client.post(self.url, {'workbook': bad}, follow=True)
        self.assertEqual(ManpowerCostSheet.objects.count(), 0)
        self.assertContains(resp, 'Could not read that workbook')

    def test_no_file_is_refused(self):
        resp = self.client.post(self.url, {}, follow=True)
        self.assertContains(resp, 'Choose a workbook')


class ImportApplyTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(name='B', is_default=True)
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('imp2', password='x', role=role)
        self.client.force_login(self.user)

    def _preview(self, rows):
        resp = self.client.post(reverse('manpowercost:sheet_import'),
                                {'workbook': workbook(rows)})
        return resp.context['payload']

    def test_apply_creates_the_sheet_and_its_lines(self):
        payload = self._preview([
            [1, 'Ali', 'PMT', 'Engineer', 12000, 850],
            [2, 'Sara', 'Admin', 'GRO', 6600, 0]])
        self.client.post(reverse('manpowercost:sheet_import_apply'),
                         {'payload': payload})
        sheet = ManpowerCostSheet.objects.get()
        self.assertEqual(sheet.lines.count(), 2)
        line = sheet.lines.order_by('order').first()
        self.assertEqual(line.employee_name, 'Ali')
        self.assertEqual(line.gross_salary, Decimal('12000'))

    def test_imported_figures_are_treated_as_monthly(self):
        """12,000 in the file is a monthly salary, so the yearly figure must
        be 144,000 - not 12,000, which is what the old app reported."""
        payload = self._preview([[1, 'Ali', 'PMT', 'Engineer', 12000, 0]])
        self.client.post(reverse('manpowercost:sheet_import_apply'),
                         {'payload': payload})
        line = ManpowerCostLine.objects.get()
        self.assertEqual(line.monthly_cost, Decimal('12000'))
        self.assertEqual(line.yearly_cost, Decimal('144000'))

    def test_a_tampered_payload_is_rejected(self):
        payload = self._preview([[1, 'Ali', 'PMT', 'Engineer', 12000, 0]])
        resp = self.client.post(reverse('manpowercost:sheet_import_apply'),
                                {'payload': payload + 'x'}, follow=True)
        self.assertEqual(ManpowerCostSheet.objects.count(), 0)
        self.assertContains(resp, 'could not be verified')

    def test_an_empty_payload_is_rejected(self):
        resp = self.client.post(reverse('manpowercost:sheet_import_apply'),
                                {'payload': ''}, follow=True)
        self.assertEqual(ManpowerCostSheet.objects.count(), 0)

    def test_apply_records_the_monthly_assumption_on_the_sheet(self):
        """The one assumption that cannot be inferred later is written down."""
        payload = self._preview([[1, 'Ali', 'PMT', 'Engineer', 12000, 0]])
        self.client.post(reverse('manpowercost:sheet_import_apply'),
                         {'payload': payload})
        self.assertIn('monthly', ManpowerCostSheet.objects.get().notes.lower())

    def test_apply_is_atomic(self):
        """A failure part-way through must leave no sheet behind.

        The old importer had no transaction, so a bad row mid-file left a
        half-imported sheet that looked complete.
        """
        from unittest.mock import patch
        payload = self._preview([
            [1, 'Ali', 'PMT', 'Engineer', 12000, 0],
            [2, 'Sara', 'Admin', 'GRO', 6600, 0]])

        real_create = ManpowerCostLine.objects.create
        calls = {'n': 0}

        def explode_on_second(*args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('boom')
            return real_create(*args, **kwargs)

        with patch.object(ManpowerCostLine.objects, 'create',
                          side_effect=explode_on_second):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('manpowercost:sheet_import_apply'),
                                 {'payload': payload})

        self.assertEqual(ManpowerCostSheet.objects.count(), 0)
        self.assertEqual(ManpowerCostLine.objects.count(), 0)

    def test_a_reader_without_edit_cannot_import(self):
        role, _ = Role.objects.get_or_create(name=Role.MANAGER)
        role.permissions.update_or_create(
            codename='manpowercost.access', defaults={'allowed': True})
        user = User.objects.create_user('reader', password='x', role=role)
        self.client.force_login(user)
        resp = self.client.get(reverse('manpowercost:sheet_import'))
        self.assertEqual(resp.status_code, 403)
