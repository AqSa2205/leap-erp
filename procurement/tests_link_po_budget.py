"""Linking an existing purchase order's lines to a budget.

Until this, `source_bom_item` could only be set by the two paths that seed a
PO from a budget, so a PO typed by hand or imported from a quotation was
invisible to the budget for good. These tests are about the four consequences
of linking one, and the three ways it must refuse.

The remaining-quantity rule is the thing to get right: this screen and the
tracker must never disagree about how much of a budget line is left, or one
will offer what the other has already spent.
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from procurement.models import PurchaseOrder


def a_region(code='EAST', name='Eastern'):
    from projects.models import Region
    region, _ = Region.objects.get_or_create(code=code, defaults={'name': name})
    return region


def a_user(username, role_name, region=None):
    role, _ = Role.objects.get_or_create(name=role_name)
    return User.objects.create_user(
        username, password='pw', role=role, region=region)


class LinkPOToBudgetTests(TestCase):

    def setUp(self):
        from costing.models import CostingLineItem, CostingSection, CostingSheet
        from projects.models import Project, ProjectStatus
        self.region = a_region()
        won, _ = ProjectStatus.objects.get_or_create(
            name='Won', defaults={'category': 'won'})
        self.project = Project.objects.create(
            project_name='Jubail', status=won, region=self.region,
            proposal_reference='REF-L-1')
        self.sheet = CostingSheet.objects.create(
            title='Jubail CCTV', project=self.project, margin=Decimal('30'),
            discount_rate=Decimal('0'), workflow_stage='finance_approved')
        self.section = CostingSection.objects.create(
            costing_sheet=self.sheet, section_number='A.1', title='Supply',
            order=0)
        self.line = CostingLineItem.objects.create(
            section=self.section, item_number='1', description='Camera 4MP',
            quantity=Decimal('10'), unit='EA', base_unit_cost=Decimal('100'),
            supplier_currency='SAR')
        self.other_line = CostingLineItem.objects.create(
            section=self.section, item_number='2', description='Cable Cat6',
            quantity=Decimal('500'), unit='M', base_unit_cost=Decimal('5'),
            supplier_currency='SAR')

        self.proc = a_user('proc', Role.PROCUREMENT_MGR, self.region)
        # The PO this feature exists for: raised by hand, no budget link.
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-HAND-1',
            vendor_name='ACME', po_issued_by='Someone',
            project=self.project, project_name='Jubail',
            created_by=self.proc, status='draft')
        self.item = self.po.items.create(
            serial_number=1, description='Camera 4MP',
            quantity=Decimal('4'), rate_per_unit=Decimal('150'), uom='EA')
        self.url = reverse('procurement:po_link_budget', args=[self.po.pk])

    def _post(self, choices):
        """choices: {po_item_pk: line_pk or ''}"""
        data = {'sheet': str(self.sheet.pk)}
        for pk, line in choices.items():
            data[f'line_{pk}'] = str(line) if line else ''
        self.client.force_login(self.proc)
        return self.client.post(self.url, data)

    # ── it works ─────────────────────────────────────────────────────────
    def test_a_hand_raised_po_line_can_be_linked(self):
        self._post({self.item.pk: self.line.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.source_bom_item, self.line)

    def test_linking_makes_the_budget_breakdown_appear(self):
        """The panel is blank for an unlinked PO because there is nothing to
        compare. That is the visible half of the feature."""
        self.assertIsNone(self.po.budget_breakdown())
        self._post({self.item.pk: self.line.pk})
        breakdown = PurchaseOrder.objects.get(pk=self.po.pk).budget_breakdown()
        self.assertIsNotNone(breakdown)
        self.assertEqual(breakdown['sheet_pk'], self.sheet.pk)

    def test_linking_stops_the_tracker_offering_the_whole_quantity(self):
        """The reason this matters more than a panel: without a link the
        tracker counts these lines as unordered and the same scope gets
        ordered twice."""
        from procurement.budget_linking import remaining
        self.assertEqual(remaining(self.line), Decimal('10'))
        self._post({self.item.pk: self.line.pk})
        self.line.refresh_from_db()
        self.assertEqual(remaining(self.line), Decimal('6'))

    def test_a_link_can_be_moved_to_another_budget_line(self):
        self._post({self.item.pk: self.line.pk})
        self._post({self.item.pk: self.other_line.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.source_bom_item, self.other_line)

    def test_a_link_can_be_removed(self):
        self._post({self.item.pk: self.line.pk})
        self._post({self.item.pk: ''})
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_saving_the_same_choice_twice_writes_nothing_new(self):
        self._post({self.item.pk: self.line.pk})
        before = self.po.items.get(pk=self.item.pk).source_bom_item_id
        resp = self._post({self.item.pk: self.line.pk})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self.po.items.get(pk=self.item.pk).source_bom_item_id, before)
        # Said rather than pretended: re-saving an untouched screen reports
        # nothing changed instead of claiming a link it did not make.
        resp = self._post({self.item.pk: self.line.pk})
        followed = self.client.get(resp.url)
        self.assertIn('Nothing changed',
                      ' '.join(str(m) for m in followed.context['messages']))

    def test_re_saving_does_not_count_the_po_against_itself(self):
        """Deliberately more than half the line: this PO takes 8 of 10, so if
        its own link counted against the remainder the second save would see
        2 left and refuse a link it already holds."""
        self.item.quantity = Decimal('8')
        self.item.save(update_fields=['quantity'])
        self._post({self.item.pk: self.line.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.source_bom_item, self.line)

        resp = self._post({self.item.pk: self.line.pk})
        self.assertEqual(resp.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.source_bom_item, self.line)

    def test_moving_a_link_within_the_same_po_frees_what_it_held(self):
        """Its own 8 must not block moving those 8 to another line."""
        self.item.quantity = Decimal('8')
        self.item.save(update_fields=['quantity'])
        self._post({self.item.pk: self.line.pk})
        self._post({self.item.pk: self.other_line.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.source_bom_item, self.other_line)

    # ── it refuses ───────────────────────────────────────────────────────
    def test_more_than_the_budget_line_has_left_is_refused(self):
        """Nearly always a mis-pick. Refusing keeps the tracker's remaining
        quantity from going negative."""
        self.item.quantity = Decimal('12')
        self.item.save(update_fields=['quantity'])
        resp = self._post({self.item.pk: self.line.pk})
        self.assertEqual(resp.status_code, 200)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_the_refusal_says_what_is_left(self):
        self.item.quantity = Decimal('12')
        self.item.save(update_fields=['quantity'])
        resp = self._post({self.item.pk: self.line.pk})
        text = ' '.join(str(m) for m in resp.context['messages'])
        self.assertIn('10', text)
        self.assertIn('12', text)

    def test_two_po_lines_cannot_share_more_than_the_line_has(self):
        """Each is under the limit; together they are over it. Checked per
        budget line across the whole screen, not per row."""
        second = self.po.items.create(
            serial_number=2, description='Camera 4MP again',
            quantity=Decimal('7'), rate_per_unit=Decimal('150'), uom='EA')
        resp = self._post({self.item.pk: self.line.pk,
                             second.pk: self.line.pk})
        self.assertEqual(resp.status_code, 200)
        self.item.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)
        self.assertIsNone(second.source_bom_item)

    def test_another_pos_links_count_against_the_remainder(self):
        other_po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-OTHER',
            vendor_name='ACME', po_issued_by='Someone',
            project=self.project, created_by=self.proc, status='issued')
        other_po.items.create(
            serial_number=1, description='Camera 4MP', quantity=Decimal('8'),
            rate_per_unit=Decimal('150'), uom='EA', source_bom_item=self.line)
        self.item.quantity = Decimal('4')
        self.item.save(update_fields=['quantity'])
        resp = self._post({self.item.pk: self.line.pk})
        self.assertEqual(resp.status_code, 200)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_a_cancelled_pos_links_do_not_count(self):
        """Same rule as the tracker: cancelled stays visible in the history
        and stops counting, or a cancelled order would block the budget."""
        cancelled = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-CANCELLED',
            vendor_name='ACME', po_issued_by='Someone',
            project=self.project, created_by=self.proc, status='cancelled')
        cancelled.items.create(
            serial_number=1, description='Camera 4MP', quantity=Decimal('10'),
            rate_per_unit=Decimal('150'), uom='EA', source_bom_item=self.line)
        self._post({self.item.pk: self.line.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.source_bom_item, self.line)

    def test_a_budget_line_from_another_sheet_is_refused(self):
        """One budget per purchase order, the same rule the tracker applies
        when appending to a draft."""
        from costing.models import CostingLineItem, CostingSection, CostingSheet
        other_sheet = CostingSheet.objects.create(
            title='Other budget', project=self.project, margin=Decimal('30'),
            discount_rate=Decimal('0'), workflow_stage='finance_approved')
        other_section = CostingSection.objects.create(
            costing_sheet=other_sheet, section_number='A.1', title='Supply',
            order=0)
        foreign = CostingLineItem.objects.create(
            section=other_section, item_number='1', description='Speaker',
            quantity=Decimal('5'), unit='EA', base_unit_cost=Decimal('80'),
            supplier_currency='SAR')
        resp = self._post({self.item.pk: foreign.pk})
        self.assertEqual(resp.status_code, 200)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_nothing_is_written_when_one_row_is_wrong(self):
        """All or nothing: a half-applied screen leaves the user working out
        which half."""
        second = self.po.items.create(
            serial_number=2, description='Cable', quantity=Decimal('50'),
            rate_per_unit=Decimal('6'), uom='M')
        self.item.quantity = Decimal('99')      # this one is over the limit
        self.item.save(update_fields=['quantity'])
        self._post({self.item.pk: self.line.pk,
                      second.pk: self.other_line.pk})
        self.item.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)
        self.assertIsNone(second.source_bom_item, 'the valid row was written anyway')

    def test_a_locked_po_is_refused(self):
        from django.utils import timezone
        PurchaseOrder.objects.filter(pk=self.po.pk).update(
            status='client_acknowledged', client_acknowledged_at=timezone.now())
        resp = self._post({self.item.pk: self.line.pk})
        self.assertEqual(resp.status_code, 302)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_a_po_with_no_project_is_refused_for_that_reason(self):
        """Distinct from "this project has no approved budget" - both end in a
        redirect, and only the message tells the user what to do next."""
        PurchaseOrder.objects.filter(pk=self.po.pk).update(project=None)
        self.client.force_login(self.proc)
        resp = self.client.get(self.url, follow=True)
        text = ' '.join(str(m) for m in resp.context['messages'])
        self.assertIn('not on a project', text)

    # ── the cash-outflow side ────────────────────────────────────────────
    def test_linking_an_issued_po_fills_the_outflow_schedule(self):
        """fill_po_numbers runs when a PO becomes committed - which for this
        PO already happened, before it had any line ids to work from."""
        from finance.models import CashOutflowRow
        PurchaseOrder.objects.filter(pk=self.po.pk).update(status='issued')
        row = CashOutflowRow.objects.create(
            project=self.project, source_ref=f'line:{self.line.pk}',
            part='A1', description='Camera 4MP', amount=Decimal('1000'))
        self._post({self.item.pk: self.line.pk})
        row.refresh_from_db()
        self.assertIn('PO-HAND-1', row.po_number)

    def test_linking_a_draft_does_not_fill_the_schedule(self):
        """A draft has not been ordered, so it covers no outflow yet - the
        same status rule the rest of the budget figures use."""
        from finance.models import CashOutflowRow
        row = CashOutflowRow.objects.create(
            project=self.project, source_ref=f'line:{self.line.pk}',
            part='A1', description='Camera 4MP', amount=Decimal('1000'))
        self._post({self.item.pk: self.line.pk})
        row.refresh_from_db()
        self.assertEqual(row.po_number or '', '')

    # ── access ───────────────────────────────────────────────────────────
    def test_a_sales_rep_cannot_link(self):
        rep = a_user('rep', Role.SALES_REP, self.region)
        self.client.force_login(rep)
        resp = self.client.post(self.url, {'sheet': str(self.sheet.pk),
                                           f'line_{self.item.pk}': str(self.line.pk)})
        self.assertEqual(resp.status_code, 302)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_a_read_only_project_manager_cannot_link(self):
        pm = a_user('pm', Role.PROJECT_MANAGER, self.region)
        self.client.force_login(pm)
        resp = self.client.post(self.url, {'sheet': str(self.sheet.pk),
                                           f'line_{self.item.pk}': str(self.line.pk)})
        self.assertEqual(resp.status_code, 302)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_a_po_in_another_region_is_not_found(self):
        """Region scoping comes from _visible_pos_for, so it bites the admin
        tier - procurement sees every region by design, and a manager is
        turned away by the role gate before the queryset is reached."""
        elsewhere = a_user('adm_west', Role.ADMIN, a_region('WEST', 'Western'))
        self.client.force_login(elsewhere)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_a_manager_is_turned_away_by_the_role_gate(self):
        manager = a_user('mgr', Role.MANAGER, self.region)
        self.client.force_login(manager)
        resp = self.client.post(self.url, {'sheet': str(self.sheet.pk),
                                           f'line_{self.item.pk}': str(self.line.pk)})
        self.assertEqual(resp.status_code, 302)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.source_bom_item)

    def test_an_admin_may_link(self):
        admin = a_user('adm', Role.ADMIN, self.region)
        self.client.force_login(admin)
        self.client.post(self.url, {'sheet': str(self.sheet.pk),
                                    f'line_{self.item.pk}': str(self.line.pk)})
        self.item.refresh_from_db()
        self.assertEqual(self.item.source_bom_item, self.line)

    # ── the page ─────────────────────────────────────────────────────────
    def test_the_page_offers_the_budget_lines_with_what_is_left(self):
        self.client.force_login(self.proc)
        resp = self.client.get(self.url + f'?sheet={self.sheet.pk}')
        self.assertEqual(resp.status_code, 200)
        left = {e['line'].pk: e['left'] for e in resp.context['budget_lines']}
        self.assertEqual(left[self.line.pk], Decimal('10'))
        self.assertEqual(left[self.other_line.pk], Decimal('500'))

    def test_the_budget_is_pinned_once_the_po_draws_on_one(self):
        self._post({self.item.pk: self.line.pk})
        self.client.force_login(self.proc)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context['pinned_sheet_id'], self.sheet.pk)

    def test_the_po_detail_page_offers_the_link_when_there_is_none(self):
        self.client.force_login(self.proc)
        resp = self.client.get(reverse('procurement:po_detail', args=[self.po.pk]))
        self.assertContains(resp, self.url)

    def test_a_linked_po_still_offers_a_way_to_correct_it(self):
        self._post({self.item.pk: self.line.pk})
        self.client.force_login(self.proc)
        resp = self.client.get(reverse('procurement:po_detail', args=[self.po.pk]))
        self.assertContains(resp, self.url)

class ValidateContractTests(TestCase):
    """validate() unit-tested as well as exercised through the screen.

    The view narrows the offered lines to the chosen budget before calling it,
    so the cross-sheet branch is unreachable from the URL - which is exactly
    why it is pinned here: it is the guard that stops a future caller (a bulk
    importer, an API) from linking a PO across two budgets.
    """

    def setUp(self):
        from costing.models import CostingLineItem, CostingSection, CostingSheet
        from projects.models import Project, ProjectStatus
        region = a_region()
        won, _ = ProjectStatus.objects.get_or_create(
            name='Won', defaults={'category': 'won'})
        project = Project.objects.create(
            project_name='V', status=won, region=region,
            proposal_reference='REF-V-1')
        self.sheets, self.lines = [], []
        for n in range(2):
            sheet = CostingSheet.objects.create(
                title=f'Sheet {n}', project=project, margin=Decimal('30'),
                discount_rate=Decimal('0'), workflow_stage='finance_approved')
            section = CostingSection.objects.create(
                costing_sheet=sheet, section_number='A.1', title='Supply',
                order=0)
            self.sheets.append(sheet)
            self.lines.append(CostingLineItem.objects.create(
                section=section, item_number='1', description=f'Thing {n}',
                quantity=Decimal('10'), unit='EA',
                base_unit_cost=Decimal('100'), supplier_currency='SAR'))
        user = a_user('v_proc', Role.PROCUREMENT_MGR, region)
        self.po = PurchaseOrder.objects.create(
            po_date=date(2026, 1, 1), po_number='PO-V-1', vendor_name='ACME',
            po_issued_by='T', project=project, created_by=user, status='draft')
        self.item = self.po.items.create(
            serial_number=1, description='Thing', quantity=Decimal('2'),
            rate_per_unit=Decimal('100'), uom='EA')

    def test_a_line_from_another_budget_is_an_error(self):
        from procurement.budget_linking import validate
        errors, changes = validate(
            self.po, self.sheets[0].pk, {self.item: self.lines[1]})
        self.assertTrue(errors)
        self.assertIn('one', errors[0].lower())
        self.assertEqual(changes, [(self.item, self.lines[1])])

    def test_a_line_from_the_chosen_budget_is_accepted(self):
        from procurement.budget_linking import validate
        errors, _changes = validate(
            self.po, self.sheets[0].pk, {self.item: self.lines[0]})
        self.assertEqual(errors, [])

    def test_an_unlink_is_never_a_cross_budget_error(self):
        from procurement.budget_linking import validate
        self.item.source_bom_item = self.lines[0]
        self.item.save(update_fields=['source_bom_item'])
        errors, changes = validate(self.po, self.sheets[1].pk, {self.item: None})
        self.assertEqual(errors, [])
        self.assertEqual(changes, [(self.item, None)])

    def test_an_unchanged_choice_is_not_a_change(self):
        from procurement.budget_linking import validate
        self.item.source_bom_item = self.lines[0]
        self.item.save(update_fields=['source_bom_item'])
        _errors, changes = validate(
            self.po, self.sheets[0].pk, {self.item: self.lines[0]})
        self.assertEqual(changes, [])
