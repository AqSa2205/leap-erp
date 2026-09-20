"""Access and rendering tests.

Three gates stack here and each is asserted separately, because the module
they replace had one hardcoded role check doing all three jobs and no way to
grant any of them independently.
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


class AccessTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(
            name='B', overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
            billable_months=Decimal('11'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'), is_default=True)
        self.sheet = ManpowerCostSheet.objects.create(
            title='S', date='2026-09-13', basis=self.basis)
        ManpowerCostLine.objects.create(
            sheet=self.sheet, employee_name='A', gross_salary=Decimal('10000'))

    def test_super_admin_can_open_the_list(self):
        self.client.force_login(make_user('sa', Role.SUPER_ADMIN))
        resp = self.client.get(reverse('manpowercost:sheet_list'))
        self.assertEqual(resp.status_code, 200)

    def test_a_role_without_the_capability_is_refused(self):
        self.client.force_login(make_user('nobody', Role.SALES_REP))
        resp = self.client.get(reverse('manpowercost:sheet_list'))
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(reverse('manpowercost:sheet_list'))
        self.assertEqual(resp.status_code, 302)

    def test_detail_opens_for_an_allowed_role(self):
        self.client.force_login(make_user('sa2', Role.SUPER_ADMIN))
        resp = self.client.get(
            reverse('manpowercost:sheet_detail', args=[self.sheet.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'S')


class PricingGateTests(TestCase):
    """Roles excluded from pricing must not reach the page at all.

    Rendering it and hiding the numbers in the template leaves them in the
    response, which is why costing made this a server-side predicate.
    """

    def setUp(self):
        CostBasis.objects.create(name='B', is_default=True)

    def _sees_403(self, role_name):
        user = make_user(f'u_{role_name}', role_name)
        # Give the capability explicitly, so the only thing that can refuse
        # is the pricing gate.
        user.role.permissions.update_or_create(
            codename='manpowercost.access', defaults={'allowed': True})
        self.client.force_login(user)
        return self.client.get(reverse('manpowercost:sheet_list')).status_code

    def test_proposal_team_cannot_see_manpower_cost(self):
        self.assertEqual(self._sees_403(Role.PROPOSAL_REP), 403)

    def test_procurement_cannot_see_manpower_cost(self):
        self.assertEqual(self._sees_403(Role.PROCUREMENT_OFF), 403)

    def test_pcc_engineer_cannot_see_manpower_cost(self):
        self.assertEqual(self._sees_403(Role.PCC_ENGINEER), 403)


class MarginGateTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(
            name='B', overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
            billable_months=Decimal('11'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'), is_default=True)
        self.position = ResourceCatalogueItem.objects.create(name='Engineer')
        ChargeRate.objects.create(
            position=self.position, basis=self.basis,
            monthly_cost=Decimal('20000'))

    def test_margin_is_absent_without_the_capability(self):
        """Not merely hidden — the view must not compute it into the context."""
        user = make_user('m1', Role.MANAGER)
        user.role.permissions.update_or_create(
            codename='manpowercost.access', defaults={'allowed': True})
        self.client.force_login(user)
        resp = self.client.get(reverse('manpowercost:rate_card'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['show_margin'])
        self.assertIsNone(resp.context['rows'][0]['margin'])

    def test_margin_is_shown_with_the_capability(self):
        self.client.force_login(make_user('sa3', Role.SUPER_ADMIN))
        resp = self.client.get(reverse('manpowercost:rate_card'))
        self.assertTrue(resp.context['show_margin'])
        self.assertIsNotNone(resp.context['rows'][0]['margin'])

    def test_the_rate_card_shows_every_derivation_step(self):
        """The build-up is the feature: a single opaque number is what made
        the source workbook uncheckable."""
        self.client.force_login(make_user('sa4', Role.SUPER_ADMIN))
        resp = self.client.get(reverse('manpowercost:rate_card'))
        build_up = resp.context['rows'][0]['build_up']
        for step in ('monthly_cost', 'overhead', 'profit',
                     'monthly_invoice', 'hourly_rate'):
            self.assertIn(step, build_up)


class RateUpdateTests(TestCase):

    def setUp(self):
        self.basis = CostBasis.objects.create(
            name='B', overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
            billable_months=Decimal('11'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'), is_default=True)
        self.rate = ChargeRate.objects.create(
            position=ResourceCatalogueItem.objects.create(name='PM'),
            basis=self.basis, monthly_cost=Decimal('20000'))
        self.url = reverse('manpowercost:rate_update', args=[self.rate.pk])

    def test_editor_can_fix_a_negotiated_rate(self):
        self.client.force_login(make_user('sa5', Role.SUPER_ADMIN))
        self.client.post(self.url, {'manual_rate': '250'})
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.manual_rate, Decimal('250'))
        self.assertTrue(self.rate.is_overridden)

    def test_blank_clears_the_override_and_follows_cost_again(self):
        self.rate.manual_rate = Decimal('250')
        self.rate.save()
        self.client.force_login(make_user('sa6', Role.SUPER_ADMIN))
        self.client.post(self.url, {'manual_rate': ''})
        self.rate.refresh_from_db()
        self.assertIsNone(self.rate.manual_rate)
        self.assertFalse(self.rate.is_overridden)

    def test_nonsense_is_rejected_without_changing_the_rate(self):
        self.client.force_login(make_user('sa7', Role.SUPER_ADMIN))
        self.client.post(self.url, {'manual_rate': 'abc'})
        self.rate.refresh_from_db()
        self.assertIsNone(self.rate.manual_rate)

    def test_a_reader_without_edit_cannot_override(self):
        user = make_user('r1', Role.MANAGER)
        for cap in ('manpowercost.access',):
            user.role.permissions.update_or_create(
                codename=cap, defaults={'allowed': True})
        self.client.force_login(user)
        resp = self.client.post(self.url, {'manual_rate': '999'})
        self.assertEqual(resp.status_code, 403)
        self.rate.refresh_from_db()
        self.assertIsNone(self.rate.manual_rate)

    def test_get_is_refused(self):
        self.client.force_login(make_user('sa8', Role.SUPER_ADMIN))
        self.assertEqual(self.client.get(self.url).status_code, 405)


class EditControlVisibilityTests(TestCase):
    """A reader must not be offered controls that would 403.

    The enforcement is the capability decorator on the view; this asserts the
    page agrees with it, so nobody is shown an Import button that refuses.
    """

    def setUp(self):
        self.basis = CostBasis.objects.create(
            name='B', overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
            billable_months=Decimal('11'), hours_per_month=Decimal('176'),
            working_days_per_month=Decimal('22'), is_default=True)
        ChargeRate.objects.create(
            position=ResourceCatalogueItem.objects.create(name='Eng'),
            basis=self.basis, monthly_cost=Decimal('20000'))
        ManpowerCostSheet.objects.create(
            title='S', date='2026-09-13', basis=self.basis)

    def _reader(self):
        user = make_user('ro', Role.MANAGER)
        user.role.permissions.update_or_create(
            codename='manpowercost.access', defaults={'allowed': True})
        return user

    def test_reader_is_not_offered_the_new_sheet_button(self):
        self.client.force_login(self._reader())
        resp = self.client.get(reverse('manpowercost:sheet_list'))
        self.assertFalse(resp.context['can_edit'])
        self.assertNotContains(resp, 'newSheetModal')

    def test_reader_is_not_offered_the_rate_edit_controls(self):
        self.client.force_login(self._reader())
        resp = self.client.get(reverse('manpowercost:rate_card'))
        self.assertFalse(resp.context['can_edit'])
        self.assertNotContains(resp, 'name="manual_rate"')

    def test_editor_is_offered_both(self):
        self.client.force_login(make_user('rw', Role.SUPER_ADMIN))
        self.assertContains(
            self.client.get(reverse('manpowercost:sheet_list')),
            'newSheetModal')
        self.assertContains(
            self.client.get(reverse('manpowercost:rate_card')),
            'name="manual_rate"')


class QueryCountTests(TestCase):
    """Assert invariance across two sizes, not a pinned number.

    A pinned count breaks on every unrelated change; what actually matters is
    that adding lines does not add queries.
    """

    def setUp(self):
        self.basis = CostBasis.objects.create(name='B', is_default=True)
        self.client.force_login(make_user('sa9', Role.SUPER_ADMIN))

    def _make(self, title, n):
        sheet = ManpowerCostSheet.objects.create(
            title=title, date='2026-09-13', basis=self.basis)
        for i in range(n):
            ManpowerCostLine.objects.create(
                sheet=sheet, employee_name=f'E{i}',
                gross_salary=Decimal('1000'))
        return sheet

    def _queries_for(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        return len(ctx)

    def test_list_query_count_is_invariant_to_sheet_count(self):
        self._make('one', 3)
        first = self._queries_for(reverse('manpowercost:sheet_list'))
        for i in range(6):
            self._make(f'extra{i}', 3)
        self.assertEqual(
            self._queries_for(reverse('manpowercost:sheet_list')), first)

    def test_detail_query_count_is_invariant_to_line_count(self):
        small = self._make('small', 2)
        large = self._make('large', 40)
        url = 'manpowercost:sheet_detail'

        def queries_for(sheet):
            from django.test.utils import CaptureQueriesContext
            from django.db import connection
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse(url, args=[sheet.pk]))
            return len(ctx)

        self.assertEqual(queries_for(small), queries_for(large))
