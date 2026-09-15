"""Tests for the on-screen cost grid and the A.4 rate wiring.

The grid replaced an Excel importer, and the importer's failure mode was
turning anything it could not parse into a silent zero. These assert the
opposite: a bad cell is refused and the old value stands.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from costing.models import ResourceCatalogueItem
from manpowercost.models import (
    ChargeRate, CostBasis, ManpowerCostLine, ManpowerCostSheet,
)


def make_user(username, role_name, caps=()):
    role, _ = Role.objects.get_or_create(name=role_name)
    user = User.objects.create_user(username, password='x', role=role)
    for codename in caps:
        role.permissions.update_or_create(
            codename=codename, defaults={'allowed': True})
    return user


class GridEditingTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(
            name='B', overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
            billable_months=Decimal('11'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'), is_default=True)
        self.sheet = ManpowerCostSheet.objects.create(
            title='S', date='2026-09-13', basis=self.basis)
        self.line = ManpowerCostLine.objects.create(
            sheet=self.sheet, employee_name='Ali',
            gross_salary=Decimal('10000'))
        self.client.force_login(make_user('ed', Role.SUPER_ADMIN))
        self.url = reverse('manpowercost:line_update', args=[self.line.pk])

    def test_a_cost_cell_saves_and_returns_the_new_totals(self):
        resp = self.client.post(self.url,
                                {'field': 'gross_salary', 'value': '12000'})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.line.refresh_from_db()
        self.assertEqual(self.line.gross_salary, Decimal('12000'))
        self.assertEqual(body['line']['monthly'], '12000.00')
        self.assertEqual(body['line']['yearly'], '144000.00')
        self.assertEqual(body['sheet']['monthly_total'], '12000.00')

    def test_a_text_cell_saves(self):
        self.client.post(self.url,
                         {'field': 'designation', 'value': 'Engineer'})
        self.line.refresh_from_db()
        self.assertEqual(self.line.designation, 'Engineer')

    def test_nonsense_is_refused_and_the_old_value_stands(self):
        """The importer this replaced would have stored 0 here."""
        resp = self.client.post(self.url,
                                {'field': 'gross_salary', 'value': 'abc'})
        self.assertEqual(resp.status_code, 400)
        self.line.refresh_from_db()
        self.assertEqual(self.line.gross_salary, Decimal('10000'))

    def test_a_negative_cost_is_refused(self):
        resp = self.client.post(self.url,
                                {'field': 'gross_salary', 'value': '-5'})
        self.assertEqual(resp.status_code, 400)
        self.line.refresh_from_db()
        self.assertEqual(self.line.gross_salary, Decimal('10000'))

    def test_a_blank_cell_means_zero(self):
        self.client.post(self.url, {'field': 'iqama_cost', 'value': '  '})
        self.line.refresh_from_db()
        self.assertEqual(self.line.iqama_cost, Decimal('0'))

    def test_an_unknown_field_is_refused_not_ignored(self):
        """Silently accepting a POST that changed nothing is
        indistinguishable from a save that worked."""
        resp = self.client.post(self.url,
                                {'field': 'sheet_id', 'value': '999'})
        self.assertEqual(resp.status_code, 400)

    def test_an_unknown_classification_is_refused(self):
        resp = self.client.post(
            self.url, {'field': 'classification', 'value': 'martian'})
        self.assertEqual(resp.status_code, 400)
        self.line.refresh_from_db()
        self.assertEqual(self.line.classification, '')

    def test_a_valid_classification_is_accepted(self):
        self.client.post(self.url,
                         {'field': 'classification', 'value': 'saudi'})
        self.line.refresh_from_db()
        self.assertEqual(self.line.classification, 'saudi')

    def test_add_and_delete_a_line(self):
        add = reverse('manpowercost:line_add', args=[self.sheet.pk])
        resp = self.client.post(add)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.sheet.lines.count(), 2)
        new_pk = resp.json()['line']['id']
        self.client.post(reverse('manpowercost:line_delete', args=[new_pk]))
        self.assertEqual(self.sheet.lines.count(), 1)

    def test_get_is_refused_on_every_grid_endpoint(self):
        for name, args in (('line_add', [self.sheet.pk]),
                           ('line_update', [self.line.pk]),
                           ('line_delete', [self.line.pk])):
            resp = self.client.get(reverse(f'manpowercost:{name}', args=args))
            self.assertEqual(resp.status_code, 405, name)


class GridAccessTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(name='B', is_default=True)
        self.sheet = ManpowerCostSheet.objects.create(
            title='S', date='2026-09-13', basis=self.basis)
        self.line = ManpowerCostLine.objects.create(
            sheet=self.sheet, gross_salary=Decimal('1000'))

    def test_a_reader_cannot_edit_a_cell(self):
        user = make_user('ro', Role.MANAGER, caps=('manpowercost.access',))
        self.client.force_login(user)
        resp = self.client.post(
            reverse('manpowercost:line_update', args=[self.line.pk]),
            {'field': 'gross_salary', 'value': '99999'})
        self.assertEqual(resp.status_code, 403)
        self.line.refresh_from_db()
        self.assertEqual(self.line.gross_salary, Decimal('1000'))

    def test_a_role_denied_pricing_cannot_edit_a_cell(self):
        user = make_user('pt', Role.PROPOSAL_REP,
                         caps=('manpowercost.access', 'manpowercost.edit'))
        self.client.force_login(user)
        resp = self.client.post(
            reverse('manpowercost:line_update', args=[self.line.pk]),
            {'field': 'gross_salary', 'value': '99999'})
        self.assertEqual(resp.status_code, 403)

    def test_creating_a_sheet_needs_edit(self):
        user = make_user('ro2', Role.MANAGER, caps=('manpowercost.access',))
        self.client.force_login(user)
        resp = self.client.post(reverse('manpowercost:sheet_create'),
                                {'title': 'Nope'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(ManpowerCostSheet.objects.count(), 1)

    def test_an_editor_can_create_a_sheet(self):
        self.client.force_login(make_user('rw', Role.SUPER_ADMIN))
        self.client.post(reverse('manpowercost:sheet_create'),
                         {'title': 'Fresh'})
        self.assertTrue(ManpowerCostSheet.objects.filter(title='Fresh').exists())

    def test_a_sheet_with_no_title_is_refused(self):
        self.client.force_login(make_user('rw2', Role.SUPER_ADMIN))
        self.client.post(reverse('manpowercost:sheet_create'), {'title': '  '})
        self.assertEqual(ManpowerCostSheet.objects.count(), 1)


class CostingA4RateTests(TestCase):
    """The standard rate reaching the A.4 grid on a costing sheet."""

    def setUp(self):
        from costing.views import _manpower_rate_lookup
        self.lookup = _manpower_rate_lookup
        self.basis = CostBasis.objects.create(
            name='B', overhead_pct=Decimal('0'), profit_pct=Decimal('0'),
            billable_months=Decimal('12'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'), is_default=True)
        # get_or_create, not create: costing/0039 already seeds this
        # catalogue, and a bare create raises on the unique name.
        self.position, _ = ResourceCatalogueItem.objects.get_or_create(
            name='Site Engineer (Civil)')

    def test_a_role_with_a_rate_is_offered(self):
        ChargeRate.objects.create(position=self.position, basis=self.basis,
                                  monthly_cost=Decimal('17600'))
        entry = self.lookup()['site engineer (civil)']
        self.assertEqual(entry['rate'], '100.00')
        self.assertEqual(entry['cost'], '100.00')
        self.assertEqual(entry['label'], 'Site Engineer (Civil)')

    def test_the_key_is_lowercased_to_survive_typed_text(self):
        """A.4 stores a typed description and resolves the catalogue link
        from it, so the join has to match free text."""
        ChargeRate.objects.create(position=self.position, basis=self.basis,
                                  monthly_cost=Decimal('17600'))
        self.assertIn('site engineer (civil)', self.lookup())

    def test_an_override_is_what_gets_offered(self):
        ChargeRate.objects.create(position=self.position, basis=self.basis,
                                  monthly_cost=Decimal('17600'),
                                  manual_rate=Decimal('250'))
        self.assertEqual(self.lookup()['site engineer (civil)']['rate'],
                         '250.00')

    def test_margin_is_reported_against_cost(self):
        ChargeRate.objects.create(position=self.position, basis=self.basis,
                                  monthly_cost=Decimal('17600'),
                                  manual_rate=Decimal('200'))
        self.assertEqual(self.lookup()['site engineer (civil)']['margin'],
                         '50.0')

    def test_roles_without_a_rate_are_absent(self):
        self.assertNotIn('site engineer (civil)', self.lookup())

    def test_the_first_classification_wins_rather_than_an_average(self):
        """Averaging two classifications would produce a number that matches
        no actual rate."""
        ChargeRate.objects.create(position=self.position, basis=self.basis,
                                  classification='saudi',
                                  monthly_cost=Decimal('17600'))
        ChargeRate.objects.create(position=self.position, basis=self.basis,
                                  classification='eastern',
                                  monthly_cost=Decimal('8800'))
        rate = Decimal(self.lookup()['site engineer (civil)']['rate'])
        self.assertIn(rate, (Decimal('100.00'), Decimal('50.00')))
