"""Sending a BOM back to proposal, and getting it back.

Sales finds something missing while costing, says what, and the sheet goes to
**Returned to Proposal**. Proposal adds it and sends it back to **Costing
started**. How long that took is recorded per round trip, because a BOM that
sat for nine working days is the thing worth finding and an overwritten single
field would never show it.

The failure modes worth guarding are quiet ones: a return with no explanation,
a clock that never stops, and a resumption that overwrites when costing
actually began.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from costing.models import BomReturn, CostingSheet
from projects.models import Project, ProjectStatus, Region

User = get_user_model()


class BomReturnWorkflowTests(TestCase):

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        region = Region.objects.create(name='KSA', code='BRT', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='open')

        def user(username, role_name):
            return User.objects.create_user(
                username, password='x', region=region,
                role=Role.objects.get(name=role_name))

        self.sales = user('br-sales', Role.SALES_REP)
        self.proposal = user('br-proposal', Role.PROPOSAL_REP)
        # A sales rep only reaches sheets on projects they own, so the owner is
        # what makes this rep the one who would be costing it.
        self.project = Project.objects.create(
            project_name='Ghazlan', proposal_reference='BRT-1',
            region=region, status=status, owner=self.sales)
        self.sheet = CostingSheet.objects.create(
            title='Ghazlan', project=self.project,
            workflow_stage='costing_in_progress')

    def _post(self, who, action, note=''):
        self.client.force_login(who)
        return self.client.post(
            reverse('costing:workflow_transition', args=[self.sheet.pk]),
            {'action': action, 'note': note}, follow=True)

    def _return_it(self, note='Fibre termination boxes are missing'):
        return self._post(self.sales, 'return_to_proposal', note)

    def _stage(self):
        self.sheet.refresh_from_db()
        return self.sheet.workflow_stage

    # ── the round trip ──────────────────────────────────────────────────────

    def test_sales_can_send_a_bom_back(self):
        self._return_it()
        self.assertEqual(self._stage(), 'returned_to_proposal')

    def test_the_comment_is_recorded(self):
        self._return_it('Fibre termination boxes are missing')
        entry = self.sheet.returns.get()
        self.assertEqual(entry.comment, 'Fibre termination boxes are missing')
        self.assertEqual(entry.returned_by, self.sales)

    def test_proposal_sends_it_back_to_costing_started(self):
        """The stage it returns to matters: sales resumes where they were
        rather than starting the sheet again."""
        self._return_it()
        self._post(self.proposal, 'resume_costing', 'Added them under Section 3')
        self.assertEqual(self._stage(), 'costing_in_progress')

    def test_it_can_also_be_sent_back_before_costing_starts(self):
        self.sheet.workflow_stage = 'ready_for_costing'
        self.sheet.save()
        self._return_it()
        self.assertEqual(self._stage(), 'returned_to_proposal')

    def test_a_bom_can_go_back_more_than_once(self):
        """Each trip is its own record — the second must not overwrite the
        first, or the history of a troublesome BOM disappears."""
        self._return_it('First thing missing')
        self._post(self.proposal, 'resume_costing')
        self._return_it('Second thing missing')
        self.assertEqual(self.sheet.returns.count(), 2)
        self.assertEqual(
            set(self.sheet.returns.values_list('comment', flat=True)),
            {'First thing missing', 'Second thing missing'})

    # ── the clock ───────────────────────────────────────────────────────────

    def test_the_clock_starts_when_it_is_sent_back(self):
        self._return_it()
        entry = self.sheet.returns.get()
        self.assertIsNotNone(entry.returned_at)
        self.assertTrue(entry.is_open)

    def test_the_clock_stops_when_proposal_sends_it_back(self):
        self._return_it()
        self._post(self.proposal, 'resume_costing')
        entry = self.sheet.returns.get()
        self.assertFalse(entry.is_open)
        self.assertEqual(entry.resolved_by, self.proposal)

    def test_an_open_return_reports_how_long_it_has_been_waiting(self):
        """Counting while open is the point — a figure that only appears once
        it stops mattering answers the wrong question."""
        self._return_it()
        entry = self.sheet.returns.get()
        BomReturn.objects.filter(pk=entry.pk).update(
            returned_at=timezone.now() - timezone.timedelta(days=7))
        entry.refresh_from_db()
        self.assertIsNotNone(entry.response_working_days)
        self.assertGreater(entry.response_working_days, 0)

    def test_the_response_is_counted_in_working_days(self):
        """Matching every other cycle figure here — a BOM returned on a
        Wednesday and fixed on Sunday took two working days, not four:
        Fri and Sat are the KSA weekend."""
        self._return_it()
        entry = self.sheet.returns.get()
        wednesday = timezone.make_aware(
            timezone.datetime(2026, 9, 2, 14, 0))          # Wed
        sunday = timezone.make_aware(
            timezone.datetime(2026, 9, 6, 9, 0))           # Sun (Fri/Sat off)
        BomReturn.objects.filter(pk=entry.pk).update(
            returned_at=wednesday, resolved_at=sunday)
        entry.refresh_from_db()
        self.assertEqual(entry.response_working_days, 2)

    def test_resuming_does_not_rewrite_when_costing_began(self):
        """costing_started_at feeds every cycle time measured from it.
        Stamping it again on a resumption would quietly shorten them all."""
        original = timezone.now() - timezone.timedelta(days=10)
        CostingSheet.objects.filter(pk=self.sheet.pk).update(
            costing_started_at=original, costing_started_by=self.sales)
        self._return_it()
        self._post(self.proposal, 'resume_costing')
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.costing_started_at, original)
        self.assertEqual(self.sheet.costing_started_by, self.sales)

    def test_resuming_stamps_it_when_it_was_never_set(self):
        """Returned straight from ready_for_costing, so costing never started
        — coming back it genuinely does."""
        self.sheet.workflow_stage = 'ready_for_costing'
        self.sheet.costing_started_at = None
        self.sheet.save()
        self._return_it()
        self._post(self.proposal, 'resume_costing')
        self.sheet.refresh_from_db()
        self.assertIsNotNone(self.sheet.costing_started_at)

    # ── guards ──────────────────────────────────────────────────────────────

    def test_a_return_without_a_comment_is_refused(self):
        """"Send it back" with no reason just moves the question to a phone
        call, and would start a clock on nothing anybody can act on."""
        self._return_it(note='')
        self.assertEqual(self._stage(), 'costing_in_progress')
        self.assertFalse(self.sheet.returns.exists())

    def test_no_return_is_recorded_when_the_transition_is_refused(self):
        """The record is written after the stage moves, so a refused
        transition cannot leave a clock running on something that never
        happened."""
        self.sheet.workflow_stage = 'finalized'
        self.sheet.save()
        self._return_it()
        self.assertEqual(self._stage(), 'finalized')
        self.assertFalse(self.sheet.returns.exists())

    def test_proposal_cannot_send_a_bom_back_to_itself(self):
        self._post(self.proposal, 'return_to_proposal', 'not mine to send')
        self.assertEqual(self._stage(), 'costing_in_progress')

    def test_sales_cannot_resolve_it_on_proposals_behalf(self):
        """The clock measures proposal's response; letting sales stop it would
        measure nothing."""
        self._return_it()
        self._post(self.sales, 'resume_costing')
        self.assertEqual(self._stage(), 'returned_to_proposal')
        self.assertTrue(self.sheet.returns.get().is_open)

    # ── visibility ──────────────────────────────────────────────────────────

    def test_the_sheet_exposes_the_open_return(self):
        self._return_it()
        self.assertIsNotNone(self.sheet.open_return)
        self._post(self.proposal, 'resume_costing')
        self.sheet.refresh_from_db()
        self.assertIsNone(self.sheet.open_return)

    def test_the_page_shows_a_banner_while_it_is_out(self):
        """A badge alone is walked past on a page this long, and being noticed
        is the whole point of the feature."""
        self._return_it('Fibre termination boxes are missing')
        self.client.force_login(self.proposal)
        body = self.client.get(
            reverse('costing:detail', args=[self.sheet.pk])).content.decode()
        self.assertIn('sales is waiting on this BOM', body)
        self.assertIn('Fibre termination boxes are missing', body)

    def test_the_banner_goes_when_it_comes_back(self):
        self._return_it()
        self._post(self.proposal, 'resume_costing')
        self.client.force_login(self.proposal)
        body = self.client.get(
            reverse('costing:detail', args=[self.sheet.pk])).content.decode()
        self.assertNotIn('sales is waiting on this BOM', body)

    def test_the_stage_badge_is_the_loud_one(self):
        from costing.models import STAGE_BADGES
        _label, css = STAGE_BADGES['returned_to_proposal']
        self.assertIn('danger', css)

    def test_a_returned_sheet_does_not_look_more_advanced_than_one_being_costed(self):
        """Index in WORKFLOW_STAGE_SEQUENCE is how far along a sheet is, and a
        return is a step back. Ordering it after costing would make a stalled
        sheet the most advanced one on its project."""
        from costing.models import WORKFLOW_STAGE_SEQUENCE
        self.assertLess(WORKFLOW_STAGE_SEQUENCE.index('returned_to_proposal'),
                        WORKFLOW_STAGE_SEQUENCE.index('costing_in_progress'))

    # ── notification ────────────────────────────────────────────────────────

    def test_proposal_is_notified_when_a_bom_comes_back(self):
        with mock.patch('costing.views.notify_users') as notify:
            self._return_it('Fibre termination boxes are missing')
        self.assertTrue(notify.called)
        recipients = notify.call_args.kwargs['recipients']
        self.assertIn(self.proposal, recipients)

    def test_the_notification_carries_what_is_missing(self):
        """A notification saying only that the stage changed sends somebody
        looking for the reason."""
        with mock.patch('costing.views.notify_users') as notify:
            self._return_it('Fibre termination boxes are missing')
        self.assertIn('Fibre termination boxes are missing',
                      notify.call_args.kwargs['description'])

    def test_sales_is_notified_when_it_comes_back(self):
        self._return_it()
        with mock.patch('costing.views.notify_users') as notify:
            self._post(self.proposal, 'resume_costing')
        self.assertTrue(notify.called)

    # ── the sheet has to be editable by the team being asked to fix it ───────

    def test_proposal_can_edit_the_sheet_while_it_is_back_with_them(self):
        """The whole point of the return is that proposal adds what is
        missing. A stage they cannot edit would make the round trip a dead
        end — asked for an item, locked out of adding it."""
        from costing.views import _user_can_edit_sheet
        self._return_it()
        self.sheet.refresh_from_db()
        self.assertTrue(_user_can_edit_sheet(self.proposal, self.sheet))

    def test_sales_cannot_edit_it_while_it_is_back_with_proposal(self):
        """Ownership is one team at a time, the same way ready_for_costing
        locks proposal out while sales holds it."""
        from costing.views import _user_can_edit_sheet
        self._return_it()
        self.sheet.refresh_from_db()
        self.assertFalse(_user_can_edit_sheet(self.sales, self.sheet))

    def test_sales_is_told_why_it_is_read_only(self):
        """A page that is silently uneditable reads as a bug."""
        from costing.views import _edit_lock_reason
        self._return_it()
        self.sheet.refresh_from_db()
        self.assertIn('Proposal', _edit_lock_reason(self.sales, self.sheet))

    def test_editing_comes_back_to_sales_when_proposal_sends_it_back(self):
        from costing.views import _user_can_edit_sheet
        self._return_it()
        self._post(self.proposal, 'resume_costing')
        self.sheet.refresh_from_db()
        self.assertTrue(_user_can_edit_sheet(self.sales, self.sheet))
        self.assertFalse(_user_can_edit_sheet(self.proposal, self.sheet))
