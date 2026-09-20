"""Managing charge rates and cost bases from the rate card.

Until this, both lived only in the Django admin: the people who price work
are not the people with admin access, so A.4 showed a dash for every role
and the rate card was read-only. The gates are asserted separately from the
behaviour, because there are three of them and they refuse for different
reasons - capability, pricing, and uniqueness.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from costing.models import ResourceCatalogueItem
from manpowercost.models import ChargeRate, CostBasis
from manpowercost.tests_views import make_user


def a_basis(name='B', **kw):
    values = dict(
        overhead_pct=Decimal('5'), profit_pct=Decimal('20'),
        billable_months=Decimal('11'), hours_per_month=Decimal('176'),
        working_days_per_month=Decimal('22'))
    values.update(kw)
    return CostBasis.objects.create(name=name, **values)


def an_editor(username):
    """Super admin: holds both the edit capability and pricing visibility."""
    return make_user(username, Role.SUPER_ADMIN)


def messages_of(response):
    """What the user was actually told.

    Asserting only that the database is unchanged passes just as happily when
    the view silently ignores the input, which is the failure this module is
    replacing - the importer that turned anything it could not parse into a
    zero and said nothing.
    """
    return [str(m) for m in response.context['messages']]


def a_reader(username):
    user = make_user(username, Role.MANAGER)
    user.role.permissions.update_or_create(
        codename='manpowercost.access', defaults={'allowed': True})
    return user


class RateCreateTests(TestCase):

    def setUp(self):
        self.basis = a_basis(is_default=True)
        self.position = ResourceCatalogueItem.objects.create(name='Test Welder')
        self.url = reverse('manpowercost:rate_create')

    def _post(self, **overrides):
        data = {'position': self.position.pk, 'basis': self.basis.pk,
                'classification': '', 'monthly_cost': '12000'}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_an_editor_can_add_a_rate(self):
        self.client.force_login(an_editor('c1'))
        self._post()
        rate = ChargeRate.objects.get()
        self.assertEqual(rate.position, self.position)
        self.assertEqual(rate.monthly_cost, Decimal('12000'))
        # A new rate follows cost. An override is a later, deliberate act.
        self.assertIsNone(rate.manual_rate)

    def test_the_same_role_class_and_basis_twice_is_refused(self):
        """The model constraint would raise IntegrityError and give a 500;
        the point of the pre-check is that the second attempt reads as a
        sentence and the first row is left untouched."""
        self.client.force_login(an_editor('c2'))
        self._post()
        self._post(monthly_cost='99999')
        self.assertEqual(ChargeRate.objects.count(), 1)
        self.assertEqual(ChargeRate.objects.get().monthly_cost,
                         Decimal('12000'))

    def test_the_same_role_on_a_different_class_is_allowed(self):
        """Jubail prices one role at several nationality bands - that spread
        is the reason classification is part of the key."""
        self.client.force_login(an_editor('c3'))
        self._post()
        self._post(classification='saudi', monthly_cost='18000')
        self.assertEqual(ChargeRate.objects.count(), 2)

    def test_a_number_that_is_not_a_number_is_refused(self):
        self.client.force_login(an_editor('c4'))
        self._post(monthly_cost='abc')
        self.assertFalse(ChargeRate.objects.exists())

    def test_a_negative_cost_is_refused(self):
        self.client.force_login(an_editor('c5'))
        self._post(monthly_cost='-1')
        self.assertFalse(ChargeRate.objects.exists())

    def test_an_unknown_classification_is_refused(self):
        """Not silently stored: a free-text band would never match the ones
        the cost sheets use, so the rate would be invisible to A.4."""
        self.client.force_login(an_editor('c6'))
        self._post(classification='martian')
        self.assertFalse(ChargeRate.objects.exists())

    def test_a_missing_position_is_refused(self):
        self.client.force_login(an_editor('c7'))
        self._post(position='')
        self.assertFalse(ChargeRate.objects.exists())

    def test_a_hand_edited_position_is_refused_not_a_500(self):
        """filter(pk=...) raises ValueError on non-numeric input rather than
        returning nothing, so this reached the user as a 500 until the pk was
        parsed first. A select is trivially editable in the browser."""
        self.client.force_login(an_editor('c11'))
        resp = self._post(position='abc')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ChargeRate.objects.exists())

    def test_a_hand_edited_basis_is_refused_not_a_500(self):
        self.client.force_login(an_editor('c12'))
        resp = self._post(basis='../../etc')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ChargeRate.objects.exists())

    def test_a_reader_cannot_add_a_rate(self):
        self.client.force_login(a_reader('c8'))
        self.assertEqual(self._post().status_code, 403)
        self.assertFalse(ChargeRate.objects.exists())

    def test_a_role_outside_pricing_cannot_add_a_rate(self):
        """Holding the edit capability is not enough: the pricing gate is a
        separate predicate, and a charge rate is a price."""
        user = make_user('c9', Role.PCC_ENGINEER)
        for cap in ('manpowercost.access', 'manpowercost.edit'):
            user.role.permissions.update_or_create(
                codename=cap, defaults={'allowed': True})
        self.client.force_login(user)
        self.assertEqual(self._post().status_code, 403)
        self.assertFalse(ChargeRate.objects.exists())

    def test_get_is_refused(self):
        self.client.force_login(an_editor('c10'))
        self.assertEqual(self.client.get(self.url).status_code, 405)


class RateCostAndBasisEditTests(TestCase):

    def setUp(self):
        self.basis = a_basis(is_default=True)
        self.other = a_basis('Site 10-hour day', hours_per_month=Decimal('260'),
                             working_days_per_month=Decimal('26'))
        self.rate = ChargeRate.objects.create(
            position=ResourceCatalogueItem.objects.create(name='Test PM'),
            basis=self.basis, monthly_cost=Decimal('20000'))
        self.url = reverse('manpowercost:rate_update', args=[self.rate.pk])

    def test_the_monthly_cost_is_editable(self):
        """A rate built from a stale cost is worse than one typed in - it
        still looks derived."""
        self.client.force_login(an_editor('u1'))
        self.client.post(self.url, {'monthly_cost': '24000'})
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.monthly_cost, Decimal('24000'))

    def test_a_nonsense_cost_leaves_the_rate_alone(self):
        self.client.force_login(an_editor('u2'))
        self.client.post(self.url, {'monthly_cost': 'twelve'})
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.monthly_cost, Decimal('20000'))

    def test_the_basis_is_switchable(self):
        self.client.force_login(an_editor('u3'))
        self.client.post(self.url, {'basis': self.other.pk})
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.basis, self.other)

    def test_switching_basis_onto_an_existing_pair_is_refused(self):
        """The unique key spans (position, classification, basis), so a basis
        change can collide. Caught before save, not as an IntegrityError."""
        ChargeRate.objects.create(
            position=self.rate.position, basis=self.other,
            monthly_cost=Decimal('30000'))
        self.client.force_login(an_editor('u4'))
        self.client.post(self.url, {'basis': self.other.pk})
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.basis, self.basis)
        self.assertEqual(ChargeRate.objects.count(), 2)

    def test_an_unknown_basis_is_refused(self):
        self.client.force_login(an_editor('u5'))
        resp = self.client.post(self.url, {'basis': '99999'}, follow=True)
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.basis, self.basis)
        # Refused out loud. Silently keeping the old basis looks identical
        # from the database side and leaves the user believing it moved.
        self.assertIn('Unknown cost basis.', messages_of(resp))

    def test_a_hand_edited_basis_is_refused_not_a_500(self):
        self.client.force_login(an_editor('u8'))
        resp = self.client.post(self.url, {'basis': 'abc'}, follow=True)
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.basis, self.basis)
        self.assertIn('Unknown cost basis.', messages_of(resp))

    def test_cost_and_override_can_move_in_one_request(self):
        """The row saves as a row: one round trip per cell would let a half
        edited rate sit on the page."""
        self.client.force_login(an_editor('u6'))
        self.client.post(self.url, {'monthly_cost': '25000',
                                    'manual_rate': '300'})
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.monthly_cost, Decimal('25000'))
        self.assertEqual(self.rate.manual_rate, Decimal('300'))

    def test_a_reader_cannot_edit_the_cost(self):
        self.client.force_login(a_reader('u7'))
        resp = self.client.post(self.url, {'monthly_cost': '1'})
        self.assertEqual(resp.status_code, 403)
        self.rate.refresh_from_db()
        self.assertEqual(self.rate.monthly_cost, Decimal('20000'))


class RateDeleteTests(TestCase):

    def setUp(self):
        self.basis = a_basis(is_default=True)
        self.rate = ChargeRate.objects.create(
            position=ResourceCatalogueItem.objects.create(name='Test Helper'),
            basis=self.basis, monthly_cost=Decimal('6000'))
        self.url = reverse('manpowercost:rate_delete', args=[self.rate.pk])

    def test_an_editor_can_remove_a_rate(self):
        self.client.force_login(an_editor('d1'))
        self.client.post(self.url)
        self.assertFalse(ChargeRate.objects.exists())

    def test_removing_a_rate_leaves_its_basis_alone(self):
        """Bases are shared. Deleting one role's rate must not take the
        parameter set every other rate is built from with it."""
        self.client.force_login(an_editor('d2'))
        self.client.post(self.url)
        self.assertTrue(CostBasis.objects.filter(pk=self.basis.pk).exists())

    def test_a_reader_cannot_remove_a_rate(self):
        self.client.force_login(a_reader('d3'))
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertTrue(ChargeRate.objects.exists())

    def test_get_is_refused(self):
        self.client.force_login(an_editor('d4'))
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertTrue(ChargeRate.objects.exists())


class BasisUpdateTests(TestCase):

    def setUp(self):
        self.basis = a_basis(is_default=True)
        self.rate = ChargeRate.objects.create(
            position=ResourceCatalogueItem.objects.create(name='Test Engineer'),
            basis=self.basis, monthly_cost=Decimal('20000'))
        self.url = reverse('manpowercost:basis_update', args=[self.basis.pk])

    def _fields(self, **overrides):
        data = {'name': self.basis.name,
                'overhead_pct': '5', 'profit_pct': '20',
                'billable_months': '11', 'hours_per_month': '176',
                'working_days_per_month': '22'}
        data.update(overrides)
        return data

    def test_editing_the_basis_moves_every_rate_built_on_it(self):
        """Nothing derived is stored, so there is no recalculation step and
        no rate can be left behind on the old parameters."""
        from manpowercost import rates
        before = rates.derive_charge_rate(
            self.rate.monthly_cost, self.basis)['hourly_rate']
        self.client.force_login(an_editor('b1'))
        self.client.post(self.url, self._fields(profit_pct='40'))
        self.basis.refresh_from_db()
        self.rate.refresh_from_db()
        after = rates.derive_charge_rate(
            self.rate.monthly_cost, self.rate.basis)['hourly_rate']
        self.assertEqual(self.basis.profit_pct, Decimal('40'))
        self.assertGreater(after, before)

    def test_zero_hours_per_month_is_refused(self):
        """It parses as a number and is not negative, so only the model
        validators catch it - and it would divide by zero on every rate."""
        self.client.force_login(an_editor('b2'))
        self.client.post(self.url, self._fields(hours_per_month='0'))
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.hours_per_month, Decimal('176'))

    def test_zero_billable_months_is_refused(self):
        self.client.force_login(an_editor('b3'))
        self.client.post(self.url, self._fields(billable_months='0'))
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.billable_months, Decimal('11'))

    def test_zero_working_days_is_refused(self):
        self.client.force_login(an_editor('b4'))
        self.client.post(self.url, self._fields(working_days_per_month='0'))
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.working_days_per_month, Decimal('22'))

    def test_a_nonsense_percentage_leaves_the_basis_alone(self):
        self.client.force_login(an_editor('b5'))
        self.client.post(self.url, self._fields(overhead_pct='ten percent'))
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.overhead_pct, Decimal('5'))

    def test_a_blank_name_is_refused(self):
        """Every derived figure names the basis it used; an unnamed one makes
        that attribution useless."""
        self.client.force_login(an_editor('b6'))
        resp = self.client.post(self.url, self._fields(name='   '), follow=True)
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.name, 'B')
        self.assertIn('A basis needs a name.', messages_of(resp))

    def test_a_duplicate_name_is_refused(self):
        a_basis('Site 10-hour day')
        self.client.force_login(an_editor('b7'))
        self.client.post(self.url, self._fields(name='Site 10-hour day'))
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.name, 'B')

    def test_the_basis_can_be_renamed(self):
        self.client.force_login(an_editor('b8'))
        self.client.post(self.url, self._fields(name='Office 8-hour day'))
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.name, 'Office 8-hour day')

    def test_a_reader_cannot_edit_the_basis(self):
        self.client.force_login(a_reader('b9'))
        resp = self.client.post(self.url, self._fields(profit_pct='99'))
        self.assertEqual(resp.status_code, 403)
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.profit_pct, Decimal('20'))

    def test_a_role_outside_pricing_cannot_edit_the_basis(self):
        user = make_user('b10', Role.PCC_ENGINEER)
        for cap in ('manpowercost.access', 'manpowercost.edit'):
            user.role.permissions.update_or_create(
                codename=cap, defaults={'allowed': True})
        self.client.force_login(user)
        resp = self.client.post(self.url, self._fields(profit_pct='99'))
        self.assertEqual(resp.status_code, 403)
        self.basis.refresh_from_db()
        self.assertEqual(self.basis.profit_pct, Decimal('20'))

    def test_get_is_refused(self):
        self.client.force_login(an_editor('b11'))
        self.assertEqual(self.client.get(self.url).status_code, 405)


class RateCardControlsTests(TestCase):
    """The page must offer exactly what the viewer can do.

    A reader shown an Add button gets a 403 for their trouble; the controls
    and the decorators have to agree.
    """

    def setUp(self):
        self.basis = a_basis(is_default=True)
        ChargeRate.objects.create(
            position=ResourceCatalogueItem.objects.create(name='Test Eng'),
            basis=self.basis, monthly_cost=Decimal('20000'))
        self.url = reverse('manpowercost:rate_card')

    def test_an_editor_is_offered_add_edit_and_delete(self):
        self.client.force_login(an_editor('v1'))
        resp = self.client.get(self.url)
        self.assertContains(resp, reverse('manpowercost:rate_create'))
        self.assertContains(resp, reverse(
            'manpowercost:basis_update', args=[self.basis.pk]))
        self.assertContains(resp, 'name="monthly_cost"')

    def test_a_reader_is_offered_none_of_them(self):
        self.client.force_login(a_reader('v2'))
        resp = self.client.get(self.url)
        self.assertNotContains(resp, reverse('manpowercost:rate_create'))
        self.assertNotContains(resp, 'data-bs-target="#new-rate"')
        self.assertNotContains(resp, reverse(
            'manpowercost:basis_update', args=[self.basis.pk]))
        self.assertNotContains(resp, 'name="monthly_cost"')
        self.assertNotContains(resp, 'name="manual_rate"')

    def test_the_position_list_is_offered_for_the_add_form(self):
        ResourceCatalogueItem.objects.create(name='Test Rigger')
        self.client.force_login(an_editor('v3'))
        resp = self.client.get(self.url)
        self.assertIn('Test Rigger', [p.name for p in resp.context['positions']])

    def test_the_rate_card_query_count_is_invariant_to_rate_count(self):
        """One extra rate must not mean one extra query per row - the basis
        dropdown and the per-basis rate count are the two places that would
        have crept in."""
        user = an_editor('v4')
        self.client.force_login(user)

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(self.url)
            return len(ctx)

        first = queries()
        for i in range(5):
            ChargeRate.objects.create(
                position=ResourceCatalogueItem.objects.create(name=f'Test R{i}'),
                basis=self.basis, monthly_cost=Decimal('10000'))
        self.assertEqual(queries(), first)
