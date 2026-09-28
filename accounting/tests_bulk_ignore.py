"""Ignoring a pasted list of Zoho accounts.

The resolver is tested as a pure function against the real shapes finance's
lists arrive in - typos, an OPEX/Personal pair that a typo sits between, names
that were never synced - and the views are tested for the two things a bulk
write must not do: act on a near miss, or discard a mapping.
"""

from django.test import TestCase
from django.urls import reverse

from accounting import bulk_ignore
from accounting.models import Account, ZohoAccountMap
from accounts.models import Role, User


class FakeRow:
    """A ZohoAccountMap as the resolver sees it, without a database.

    The resolver reads three attributes; giving it three keeps these tests
    about name resolution rather than about fixtures.
    """

    _next_pk = 1

    def __init__(self, name, account_id=None, is_ignored=False):
        self.pk = FakeRow._next_pk
        FakeRow._next_pk += 1
        self.zoho_account_name = name
        self.account_id = account_id
        self.is_ignored = is_ignored


def outcomes(names, rows):
    return [(r.name, r.outcome) for r in bulk_ignore.resolve(names, rows)]


class ParseNamesTests(TestCase):

    def test_one_name_per_line_blanks_dropped(self):
        self.assertEqual(
            bulk_ignore.parse_names('Alpha\n\n  Beta  \n\n'),
            ['Alpha', 'Beta'])

    def test_internal_whitespace_is_collapsed(self):
        """The pasted list carries double spaces that the account names do
        not - 'muhammad Ahmed  OPEX' arrived that way."""
        self.assertEqual(bulk_ignore.parse_names('A   B'), ['A B'])

    def test_repeats_collapse_however_they_are_cased(self):
        self.assertEqual(
            bulk_ignore.parse_names('Alpha\nALPHA\nalpha '), ['Alpha'])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(bulk_ignore.parse_names(''), [])
        self.assertEqual(bulk_ignore.parse_names(None), [])


class ResolveTests(TestCase):

    def test_an_exact_unmapped_name_is_matched(self):
        rows = [FakeRow('Sharib Abbas')]
        self.assertEqual(outcomes(['Sharib Abbas'], rows),
                         [('Sharib Abbas', bulk_ignore.MATCHED)])

    def test_case_and_spacing_do_not_matter(self):
        rows = [FakeRow('Sharib Abbas')]
        self.assertEqual(outcomes(['  sharib   ABBAS '], rows)[0][1],
                         bulk_ignore.MATCHED)

    def test_a_typo_is_offered_not_resolved(self):
        """'Sharib ABbbas' is obviously 'Sharib Abbas' to a person. It is
        still not actioned - see the module docstring for the pair that makes
        auto-correcting dangerous."""
        rows = [FakeRow('Sharib Abbas')]
        res = bulk_ignore.resolve(['Sharib ABbbas'], rows)[0]
        self.assertEqual(res.outcome, bulk_ignore.SIMILAR)
        self.assertEqual([r.zoho_account_name for r in res.rows],
                         ['Sharib Abbas'])

    def test_a_typo_between_two_real_accounts_offers_both(self):
        """The case that decides the whole design: 'M Shahab anwar' is not an
        account. Zoho has an OPEX and a Personal, and picking either would be
        a guess at somebody's money."""
        rows = [FakeRow('M Shahab Anwar OPEX'), FakeRow('M Shahab Anwar Personal')]
        res = bulk_ignore.resolve(['M Shahab anwar'], rows)[0]
        self.assertEqual(res.outcome, bulk_ignore.SIMILAR)
        self.assertEqual(len(res.rows), 2)
        self.assertEqual(bulk_ignore.ignorable_pks([res]), [])

    def test_opex_and_plain_are_never_confused_when_both_are_exact(self):
        """normalise() keeps 'OPEX' deliberately - these are two accounts."""
        rows = [FakeRow('Mohammad Faisal Zaidi'),
                FakeRow('Mohammad Faisal Zaidi OPEX')]
        res = bulk_ignore.resolve(
            ['Mohammad Faisal Zaidi', 'Mohammad Faisal Zaidi OPEX'], rows)
        self.assertEqual([r.outcome for r in res],
                         [bulk_ignore.MATCHED, bulk_ignore.MATCHED])
        self.assertEqual(
            [r.row.zoho_account_name for r in res],
            ['Mohammad Faisal Zaidi', 'Mohammad Faisal Zaidi OPEX'])

    def test_a_mapped_row_is_refused_not_ignored(self):
        """Ignoring it would throw the mapping away, which a list of names
        did not ask for."""
        rows = [FakeRow('Bank Charges', account_id=7)]
        res = bulk_ignore.resolve(['Bank Charges'], rows)[0]
        self.assertEqual(res.outcome, bulk_ignore.ALREADY_MAPPED)
        self.assertEqual(bulk_ignore.ignorable_pks([res]), [])

    def test_an_already_ignored_row_is_a_no_op(self):
        rows = [FakeRow('Parking', is_ignored=True)]
        res = bulk_ignore.resolve(['Parking'], rows)[0]
        self.assertEqual(res.outcome, bulk_ignore.ALREADY_IGNORED)
        self.assertEqual(bulk_ignore.ignorable_pks([res]), [])

    def test_the_same_name_twice_in_zoho_is_a_choice_not_a_match(self):
        rows = [FakeRow('Medical Expenses'), FakeRow('Medical Expenses')]
        res = bulk_ignore.resolve(['Medical Expenses'], rows)[0]
        self.assertEqual(res.outcome, bulk_ignore.DUPLICATE)
        self.assertEqual(len(res.rows), 2)
        self.assertEqual(bulk_ignore.ignorable_pks([res]), [])

    def test_a_name_that_was_never_synced_says_so(self):
        rows = [FakeRow('Parking')]
        res = bulk_ignore.resolve(['Something Else Entirely'], rows)[0]
        self.assertEqual(res.outcome, bulk_ignore.NONE)
        self.assertEqual(res.rows, [])

    def test_only_exact_unmapped_matches_are_ticked(self):
        rows = [FakeRow('Parking'), FakeRow('Postage', account_id=3),
                FakeRow('Lodging', is_ignored=True), FakeRow('Sharib Abbas')]
        res = bulk_ignore.resolve(
            ['Parking', 'Postage', 'Lodging', 'Sharib ABbbas', 'Nothing'], rows)
        ticked = bulk_ignore.ignorable_pks(res)
        self.assertEqual(ticked, [rows[0].pk])

    def test_groups_come_back_in_a_fixed_order(self):
        rows = [FakeRow('Parking'), FakeRow('Sharib Abbas')]
        res = bulk_ignore.resolve(['Nothing', 'Sharib ABbbas', 'Parking'], rows)
        self.assertEqual(list(bulk_ignore.group(res)),
                         [bulk_ignore.MATCHED, bulk_ignore.SIMILAR,
                          bulk_ignore.NONE])


def a_user(username, role_name):
    role, _ = Role.objects.get_or_create(name=role_name)
    return User.objects.create_user(username, password='pw', role=role)


class BulkIgnoreViewTests(TestCase):

    def setUp(self):
        self.preview_url = reverse('accounting:zoho_mapping_bulk_ignore')
        self.apply_url = reverse('accounting:zoho_mapping_bulk_ignore_apply')
        self.account = Account.objects.create(
            code='5000001', name='Bank Charges',
            internal_type=Account.TYPE_REGULAR)
        self.plain = ZohoAccountMap.objects.create(
            zoho_account_id='z1', zoho_account_name='Parking',
            zoho_account_type='expense')
        self.mapped = ZohoAccountMap.objects.create(
            zoho_account_id='z2', zoho_account_name='Bank Charges',
            zoho_account_type='expense', account=self.account)
        self.opex = ZohoAccountMap.objects.create(
            zoho_account_id='z3', zoho_account_name='M Shahab Anwar OPEX',
            zoho_account_type='other_current_asset')
        self.personal = ZohoAccountMap.objects.create(
            zoho_account_id='z4', zoho_account_name='M Shahab Anwar Personal',
            zoho_account_type='other_current_asset')
        self.finance = a_user('fin', Role.FINANCE_HEAD)

    # ── preview ──────────────────────────────────────────────────────────
    def test_the_preview_changes_nothing(self):
        self.client.force_login(self.finance)
        resp = self.client.post(self.preview_url, {'names': 'Parking'})
        self.assertEqual(resp.status_code, 200)
        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_ignored)

    def test_the_preview_ticks_the_exact_match_only(self):
        self.client.force_login(self.finance)
        resp = self.client.post(self.preview_url, {
            'names': 'Parking\nM Shahab anwar\nBank Charges\nNo Such Account'})
        preticked = resp.context['preticked']
        self.assertEqual(preticked, {self.plain.pk})
        self.assertNotIn(self.opex.pk, preticked)
        self.assertNotIn(self.mapped.pk, preticked)

    def test_the_preview_shows_every_pasted_name_somewhere(self):
        """A name that quietly vanished from the preview is the worst failure
        here: it reads as dealt with."""
        self.client.force_login(self.finance)
        names = ['Parking', 'M Shahab anwar', 'Bank Charges', 'No Such Account']
        resp = self.client.post(self.preview_url, {'names': '\n'.join(names)})
        listed = [res.name for g in resp.context['groups'] for res in g['rows']]
        self.assertEqual(sorted(listed), sorted(names))

    def test_an_empty_paste_goes_back_with_a_message(self):
        self.client.force_login(self.finance)
        resp = self.client.post(self.preview_url, {'names': '   \n\n'})
        self.assertEqual(resp.status_code, 302)

    # ── apply ────────────────────────────────────────────────────────────
    def test_applying_ignores_the_ticked_rows(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        self.plain.refresh_from_db()
        self.assertTrue(self.plain.is_ignored)

    def test_applying_records_who_did_it(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        self.plain.refresh_from_db()
        self.assertIn('fin', self.plain.note)
        self.assertIn('bulk', self.plain.note.lower())

    def test_applying_leaves_untouched_rows_alone(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        for row in (self.opex, self.personal):
            row.refresh_from_db()
            self.assertFalse(row.is_ignored)

    def test_a_mapped_row_is_never_ignored_even_if_posted(self):
        """The preview does not offer it, so reaching here means a stale page
        or a hand-made post. Either way the mapping survives - ignored and
        mapped must never both be true."""
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'ignore': [str(self.mapped.pk)]})
        self.mapped.refresh_from_db()
        self.assertFalse(self.mapped.is_ignored)
        self.assertEqual(self.mapped.account, self.account)

    def test_a_row_mapped_since_the_preview_is_left_alone(self):
        """The reason apply re-checks instead of trusting the preview."""
        self.client.force_login(self.finance)
        resp = self.client.post(self.preview_url, {'names': 'Parking'})
        self.assertEqual(resp.context['preticked'], {self.plain.pk})
        ZohoAccountMap.objects.filter(pk=self.plain.pk).update(
            account=self.account)
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_ignored)
        self.assertEqual(self.plain.account, self.account)

    def test_rubbish_pks_are_dropped_rather_than_crashing(self):
        self.client.force_login(self.finance)
        resp = self.client.post(self.apply_url,
                                {'ignore': ['abc', '', '99999999']})
        self.assertEqual(resp.status_code, 302)

    def test_applying_twice_is_a_no_op_the_second_time(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        self.plain.refresh_from_db()
        first_note = self.plain.note
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        self.plain.refresh_from_db()
        self.assertTrue(self.plain.is_ignored)
        self.assertEqual(self.plain.note, first_note)

    # ── access ───────────────────────────────────────────────────────────
    def test_a_super_admin_may_use_it(self):
        self.client.force_login(a_user('boss', Role.SUPER_ADMIN))
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        self.plain.refresh_from_db()
        self.assertTrue(self.plain.is_ignored)

    def test_somebody_outside_finance_is_refused(self):
        """Same gate as the rest of the chart - finance owns it."""
        self.client.force_login(a_user('rep', Role.SALES_REP))
        for url in (self.preview_url, self.apply_url):
            with self.subTest(url=url):
                resp = self.client.post(url, {'names': 'Parking',
                                              'ignore': [str(self.plain.pk)]})
                self.assertEqual(resp.status_code, 403)
        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_ignored)

    def test_get_is_refused_on_both(self):
        self.client.force_login(self.finance)
        for url in (self.preview_url, self.apply_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.post(self.apply_url,
                                {'ignore': [str(self.plain.pk)]})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)
        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_ignored)

    # ── the worklist afterwards ──────────────────────────────────────────
    def test_an_ignored_row_leaves_the_worklist_and_shows_under_ignored(self):
        self.client.force_login(self.finance)
        self.client.post(self.apply_url, {'ignore': [str(self.plain.pk)]})
        unmapped = self.client.get(reverse('accounting:zoho_mapping'))
        ignored = self.client.get(
            reverse('accounting:zoho_mapping') + '?state=ignored')
        on_worklist = [r.pk for r in unmapped.context['page'].object_list]
        on_ignored = [r.pk for r in ignored.context['page'].object_list]
        self.assertNotIn(self.plain.pk, on_worklist)
        self.assertIn(self.plain.pk, on_ignored)
