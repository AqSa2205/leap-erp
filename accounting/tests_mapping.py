"""Mapping Zoho's chart onto the ERP's — the suggestion engine and the screen.

Zoho Books for this organisation has no account codes, so the code-match automap
proposes nothing and names are the only signal available. That makes these tests
mostly about *refusing* to answer: the ERP chart carries the same name under
different codes (Medical Expenses is both 4100013 administrative and 5000009
project), and near-identical pairs like `Adil Abbas` and `Adil Abbas OPEX` are
different accounts belonging to the same person.

A wrong mapping misposts money silently and surfaces at a reconciliation months
later. An unmapped row costs somebody a minute. The asymmetry is the design.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounting.mapping import certain_matches, index_accounts, suggest
from accounting.models import Account, ZohoAccountMap

User = get_user_model()


class SuggestionEngineTests(TestCase):
    """Which ERP account a Zoho name points at, and when to decline to say."""

    def setUp(self):
        self.heading = Account.objects.create(
            code='4000000', name='Cost of Sale', internal_type='View')

        def account(code, name):
            return Account.objects.create(code=code, name=name,
                                          internal_type='Regular', parent=self.heading)

        self.local = account('4100006', 'Local Procurement')
        self.adil = account('4100100', 'Adil Abbas')
        self.adil_opex = account('4100101', 'Adil Abbas OPEX')
        # The real trap, straight from the live chart: this name exists twice
        # under different codes — administrative and project.
        self.medical_admin = account('4100013', 'Medical Expenses')
        self.medical_project = account('5000009', 'Medical Expenses')

    def _suggest(self, name):
        return suggest(name, index_accounts(Account.objects.postable()))

    def test_a_unique_exact_match_is_certain(self):
        suggestion = self._suggest('Local Procurement')
        self.assertEqual(suggestion.kind, 'certain')
        self.assertEqual(suggestion.account, self.local)
        self.assertTrue(suggestion.is_certain)

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(self._suggest('  local   PROCUREMENT ').account, self.local)

    def test_a_name_matching_two_accounts_is_ambiguous_not_certain(self):
        """The most dangerous case in the whole feature: an exact match to
        several accounts reads as the strongest possible signal, and picking
        either one misposts without complaint."""
        suggestion = self._suggest('Medical Expenses')
        self.assertEqual(suggestion.kind, 'ambiguous')
        self.assertIsNone(suggestion.account)
        self.assertFalse(suggestion.is_certain)
        self.assertCountEqual(suggestion.candidates,
                              [self.medical_admin, self.medical_project])

    def test_opex_is_not_folded_away(self):
        """A normaliser that stripped the suffix as noise would merge two of
        one person's accounts into one."""
        self.assertEqual(self._suggest('Adil Abbas').account, self.adil)
        self.assertEqual(self._suggest('Adil Abbas OPEX').account, self.adil_opex)

    def test_a_close_name_is_offered_but_never_certain(self):
        suggestion = self._suggest('Local Procurements')
        self.assertEqual(suggestion.kind, 'similar')
        self.assertIsNone(suggestion.account)
        self.assertIn(self.local, suggestion.candidates)

    def test_an_unrelated_name_suggests_nothing(self):
        suggestion = self._suggest('Zoho Internal Clearing Widget')
        self.assertEqual(suggestion.kind, 'none')
        self.assertFalse(suggestion.is_actionable)

    def test_a_blank_name_suggests_nothing(self):
        self.assertEqual(self._suggest('').kind, 'none')

    def test_a_heading_is_never_suggested(self):
        """A heading cannot be posted to, so suggesting one offers a mapping
        that breaks at the first transaction rather than here."""
        suggestion = self._suggest('Cost of Sale')
        self.assertIsNone(suggestion.account)
        self.assertNotIn(self.heading, suggestion.candidates)

    def test_certain_matches_skips_the_ambiguous_one(self):
        good = ZohoAccountMap.objects.create(zoho_account_id='Z1',
                                             zoho_account_name='Local Procurement')
        bad = ZohoAccountMap.objects.create(zoho_account_id='Z2',
                                            zoho_account_name='Medical Expenses')
        matches = certain_matches([good, bad], index_accounts(Account.objects.postable()))
        self.assertEqual(matches, {good.pk: self.local})


class ZohoMappingScreenTests(TestCase):
    """The worklist screen: what it shows, what it saves, what it refuses."""

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.finance = User.objects.create_user(
            'zm-super', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.outsider = User.objects.create_user(
            'zm-out', password='x',
            role=Role.objects.get(name=Role.DOCUMENT_CONTROLLER))

        self.heading = Account.objects.create(code='4000000', name='Cost of Sale',
                                              internal_type='View')

        def account(code, name):
            return Account.objects.create(code=code, name=name,
                                          internal_type='Regular', parent=self.heading)

        self.local = account('4100006', 'Local Procurement')
        self.intl = account('4100007', 'International Procurement')
        self.medical_a = account('4100013', 'Medical Expenses')
        self.medical_b = account('5000009', 'Medical Expenses')

        self.exact = ZohoAccountMap.objects.create(
            zoho_account_id='Z1', zoho_account_name='Local Procurement',
            zoho_account_type='expense')
        self.ambiguous = ZohoAccountMap.objects.create(
            zoho_account_id='Z2', zoho_account_name='Medical Expenses',
            zoho_account_type='expense')
        self.orphan = ZohoAccountMap.objects.create(
            zoho_account_id='Z3', zoho_account_name='Something Only Zoho Has',
            zoho_account_type='other_current_asset')

        self.url = reverse('accounting:zoho_mapping')
        self.save_url = reverse('accounting:zoho_mapping_save')
        self.apply_url = reverse('accounting:zoho_mapping_apply_certain')

    def _as_finance(self):
        self.client.force_login(self.finance)

    # ── access ──────────────────────────────────────────────────────────────

    def test_someone_outside_finance_cannot_open_it(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_someone_outside_finance_cannot_bulk_apply(self):
        self.client.force_login(self.outsider)
        self.client.post(self.apply_url)
        self.assertEqual(ZohoAccountMap.objects.mapped().count(), 0)

    def test_someone_outside_finance_cannot_save(self):
        self.client.force_login(self.outsider)
        self.client.post(self.save_url, {'row': [self.exact.pk],
                                         f'account_{self.exact.pk}': self.local.pk})
        self.exact.refresh_from_db()
        self.assertIsNone(self.exact.account)

    def test_the_mutations_refuse_a_get(self):
        """A mapping change must not be reachable by following a link."""
        self._as_finance()
        self.assertEqual(self.client.get(self.apply_url).status_code, 405)
        self.assertEqual(self.client.get(self.save_url).status_code, 405)

    # ── the page ────────────────────────────────────────────────────────────

    def test_the_counts_describe_the_whole_table_not_the_filtered_page(self):
        """These numbers are what somebody tracks across a long grind. They
        must not move when the search box is typed in."""
        self._as_finance()
        everything = self.client.get(self.url)
        filtered = self.client.get(self.url, {'q': 'Local'})
        self.assertEqual(filtered.context['total_count'],
                         everything.context['total_count'])
        self.assertEqual(filtered.context['unmapped_count'],
                         everything.context['unmapped_count'])
        self.assertLess(len(filtered.context['rows']),
                        everything.context['total_count'])

    def test_only_the_unambiguous_match_is_offered_for_bulk_apply(self):
        self._as_finance()
        self.assertEqual(self.client.get(self.url).context['ready_count'], 1)

    def test_the_ambiguous_row_shows_both_candidates(self):
        self._as_finance()
        rows = {row.pk: row for row in self.client.get(self.url).context['rows']}
        suggestion = rows[self.ambiguous.pk].suggestion
        self.assertEqual(suggestion.kind, 'ambiguous')
        self.assertCountEqual(suggestion.candidates, [self.medical_a, self.medical_b])

    def test_only_postable_accounts_are_offered(self):
        self._as_finance()
        self.assertNotIn(self.heading, self.client.get(self.url).context['accounts'])

    def test_the_query_count_does_not_grow_with_the_rows(self):
        """Every row renders its ERP account, so a missing select_related here
        is one query per row and degrades quietly as mapping progresses."""
        self._as_finance()

        def queries_for(n):
            ZohoAccountMap.objects.exclude(pk=self.exact.pk).delete()
            for i in range(n):
                ZohoAccountMap.objects.create(
                    zoho_account_id=f'BULK{i}', zoho_account_name=f'Bulk {i}',
                    account=self.local)
            with _count(self) as ctx:
                self.client.get(self.url, {'state': 'all'})
            return ctx.count

        self.assertEqual(queries_for(3), queries_for(12))

    # ── bulk apply ──────────────────────────────────────────────────────────

    def test_bulk_apply_maps_the_unambiguous_row(self):
        self._as_finance()
        self.client.post(self.apply_url)
        self.exact.refresh_from_db()
        self.assertEqual(self.exact.account, self.local)

    def test_bulk_apply_leaves_the_ambiguous_row_alone(self):
        self._as_finance()
        self.client.post(self.apply_url)
        self.ambiguous.refresh_from_db()
        self.assertIsNone(self.ambiguous.account)

    def test_bulk_apply_never_overwrites_a_decision_already_made(self):
        """Finance's own mapping outranks any match the machine found."""
        self.exact.account = self.intl          # deliberately not the name match
        self.exact.save()
        self._as_finance()
        self.client.post(self.apply_url)
        self.exact.refresh_from_db()
        self.assertEqual(self.exact.account, self.intl)

    def test_bulk_apply_records_how_it_happened(self):
        self._as_finance()
        self.client.post(self.apply_url)
        self.exact.refresh_from_db()
        self.assertIn('exact name match', self.exact.note)
        self.assertIn('zm-super', self.exact.note)

    # ── saving one page ─────────────────────────────────────────────────────

    def test_saving_maps_a_row(self):
        self._as_finance()
        self.client.post(self.save_url, {
            'row': [self.ambiguous.pk],
            f'account_{self.ambiguous.pk}': self.medical_b.pk,
        })
        self.ambiguous.refresh_from_db()
        self.assertEqual(self.ambiguous.account, self.medical_b)

    def test_saving_can_clear_a_mapping(self):
        self.exact.account = self.local
        self.exact.save()
        self._as_finance()
        self.client.post(self.save_url,
                         {'row': [self.exact.pk], f'account_{self.exact.pk}': ''})
        self.exact.refresh_from_db()
        self.assertIsNone(self.exact.account)

    def test_a_heading_is_refused_even_when_posted_directly(self):
        """The select only offers postable accounts, so this is a forged or
        stale post. A heading cannot be posted to, and accepting one would
        break at the first transaction instead of here."""
        self._as_finance()
        self.client.post(self.save_url, {'row': [self.exact.pk],
                                         f'account_{self.exact.pk}': self.heading.pk})
        self.exact.refresh_from_db()
        self.assertIsNone(self.exact.account)

    def test_ignoring_takes_a_row_off_the_worklist(self):
        self._as_finance()
        self.client.post(self.save_url, {
            'row': [self.orphan.pk],
            f'account_{self.orphan.pk}': '',
            f'ignore_{self.orphan.pk}': 'on',
        })
        self.orphan.refresh_from_db()
        self.assertTrue(self.orphan.is_ignored)
        self.assertNotIn(self.orphan, ZohoAccountMap.objects.unmapped())

    def test_mapping_a_row_clears_any_ignore_on_it(self):
        """Mapped and ignored together is a contradiction — one says it has a
        home, the other says it deliberately has none."""
        self.orphan.is_ignored = True
        self.orphan.save()
        self._as_finance()
        self.client.post(self.save_url, {
            'row': [self.orphan.pk],
            f'account_{self.orphan.pk}': self.local.pk,
            f'ignore_{self.orphan.pk}': 'on',
        })
        self.orphan.refresh_from_db()
        self.assertEqual(self.orphan.account, self.local)
        self.assertFalse(self.orphan.is_ignored)

    def test_resubmitting_an_unchanged_row_does_not_stamp_it_again(self):
        """Otherwise paging back and forth buries the real history in noise."""
        self.exact.account = self.local
        self.exact.note = 'original note'
        self.exact.save()
        self._as_finance()
        self.client.post(self.save_url, {'row': [self.exact.pk],
                                         f'account_{self.exact.pk}': self.local.pk})
        self.exact.refresh_from_db()
        self.assertEqual(self.exact.note, 'original note')

    def test_saving_one_row_does_not_disturb_the_others(self):
        self._as_finance()
        self.client.post(self.save_url, {
            'row': [self.exact.pk, self.ambiguous.pk],
            f'account_{self.exact.pk}': self.local.pk,
            f'account_{self.ambiguous.pk}': '',
        })
        self.ambiguous.refresh_from_db()
        self.assertIsNone(self.ambiguous.account)
        self.assertEqual(self.ambiguous.note, '')


class _count:
    """Count queries without pinning a number.

    A fixed expected count breaks on any unrelated query change and, worse,
    invites being updated to whatever the code happens to do — which is how an
    N+1 gets accepted rather than fixed. Comparing two data sizes asserts the
    property that actually matters.
    """

    def __init__(self, testcase):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self._inner = CaptureQueriesContext(connection)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        self._inner.__exit__(*exc)
        return False

    @property
    def count(self):
        return len(self._inner.captured_queries)
