"""A.4 resources — the build-up behind the A.2 services price.

Two properties carry this feature, and both are the kind that fail silently.

A.4 **is** the A.2 figure when it has lines, so editing a manpower rate moves
the contract total on a client-facing quote. And A.4 must **never** appear on
that quote — the client is buying a service, not being shown the welder rates
it was assembled from. A leak there is a commercial problem, not a cosmetic
one, and nothing about the page would look wrong.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from costing.models import (
    CostingSheet, ResourceCatalogueItem, ResourceLine, ScopeOfWorkItem,
)
from projects.models import Project, ProjectStatus, Region

User = get_user_model()


class ResourceCatalogueTests(TestCase):
    """The picklist, as the seed migration leaves it."""

    def test_the_catalogue_is_seeded(self):
        self.assertTrue(ResourceCatalogueItem.objects.exists())

    def test_it_holds_the_resources_finance_listed(self):
        names = set(ResourceCatalogueItem.objects.values_list('name', flat=True))
        for expected in ('Project Manager', 'Welder', 'Helper', 'Accomodation',
                         'Aramco Approved Splicer', 'Consumables'):
            self.assertIn(expected, names)

    def test_no_entry_appears_twice(self):
        """Finance's list had Accomodation and Food Expense in it twice, once
        among the civil trades and once among the telecom ones. A picklist
        cannot usefully offer the same entry twice — you cannot tell which one
        you picked — and nothing is lost, since a sheet can carry the same
        resource on two lines."""
        names = list(ResourceCatalogueItem.objects.values_list('name', flat=True))
        self.assertEqual(len(names), len(set(names)))

    def test_it_keeps_finance_ordering(self):
        first = ResourceCatalogueItem.objects.order_by('order').first()
        self.assertEqual(first.name, 'Project Manager')


class ResourceTotalTests(TestCase):
    """What A.4 does to the A.2 figure."""

    def setUp(self):
        self.sheet = CostingSheet.objects.create(title='Ghazlan',
                                                 output_currency='SAR')

    def _line(self, description, qty, rate):
        return ResourceLine.objects.create(
            costing_sheet=self.sheet, description=description,
            quantity=Decimal(qty), rate=Decimal(rate), serial_number=1)

    def _sow(self, description, total):
        return ScopeOfWorkItem.objects.create(
            costing_sheet=self.sheet, description=description,
            total_price=Decimal(total))

    def test_a_line_totals_quantity_times_rate(self):
        line = self._line('Welder', '3', '250.50')
        self.assertEqual(line.total_price, Decimal('751.50'))

    def test_the_resources_total_is_the_sum_of_its_lines(self):
        self._line('Project Manager', '1', '20000')
        self._line('Helper', '4', '2500')
        self.assertEqual(self.sheet.resources_total, Decimal('30000'))

    def test_the_resources_total_becomes_the_a2_figure(self):
        """The decision this feature rests on: A.4 is how the services price
        is arrived at, so it IS that price."""
        self._line('Project Manager', '1', '20000')
        self.assertEqual(self.sheet.sow_total, Decimal('20000'))

    def test_a4_outranks_the_a2_rows(self):
        """Those rows describe what the client is buying — they frequently
        carry price_text like 'Included' rather than a figure — so when the
        build-up exists it is the arithmetic, not them."""
        self._sow('Installation & commissioning', '999')
        self._line('Project Manager', '1', '20000')
        self.assertEqual(self.sheet.sow_total, Decimal('20000'))

    def test_without_a4_the_a2_rows_still_decide(self):
        """Every sheet quoted before this existed has to keep its number."""
        self._sow('Installation & commissioning', '999')
        self.assertEqual(self.sheet.sow_total, Decimal('999'))

    def test_with_neither_the_legacy_flat_field_still_decides(self):
        self.sheet.scope_of_work_total = Decimal('123')
        self.sheet.save()
        self.assertEqual(self.sheet.sow_total, Decimal('123'))

    def test_deleting_the_last_line_hands_a2_back_to_its_own_rows(self):
        """The total can then move by more than the line removed, which is why
        the delete endpoint returns it."""
        self._sow('Installation & commissioning', '999')
        line = self._line('Project Manager', '1', '20000')
        self.assertEqual(self.sheet.sow_total, Decimal('20000'))
        line.delete()
        self.assertEqual(self.sheet.sow_total, Decimal('999'))

    def test_the_contract_total_moves_with_the_resources(self):
        """End to end: a manpower rate changes what the client is charged."""
        before = self.sheet.contract_total
        self._line('Project Manager', '1', '20000')
        self.assertEqual(self.sheet.contract_total - before, Decimal('20000'))


class ResourcesNeverPrintTests(TestCase):
    """A.4 must not reach the client's quote."""

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.user = User.objects.create_user(
            'a4-super', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        region = Region.objects.create(name='KSA', code='A4T', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='open')
        self.project = Project.objects.create(
            project_name='Ghazlan', proposal_reference='A4T-1',
            region=region, status=status)
        self.sheet = CostingSheet.objects.create(
            title='Ghazlan', project=self.project, output_currency='SAR')
        ResourceLine.objects.create(
            costing_sheet=self.sheet, description='Aramco Approved Splicer',
            quantity=Decimal('2'), rate=Decimal('17500'), serial_number=1)

    def _pdf_text(self):
        """Text extracted from the PDF, not its raw bytes.

        ReportLab compresses text streams, so `assertNotIn(b'...', content)`
        can pass because the string was compressed rather than because it was
        never rendered — a "must not appear" assertion that cannot fail is
        worse than none. Proven here: 'Resources' is present in the raw bytes
        of this very document and absent from its text.
        """
        import io as _io

        from pypdf import PdfReader

        self.client.force_login(self.user)
        response = self.client.get(
            reverse('costing:export_pdf', args=[self.sheet.pk]))
        self.assertEqual(response.status_code, 200)
        reader = PdfReader(_io.BytesIO(response.content))
        return '\n'.join((page.extract_text() or '') for page in reader.pages)

    def test_the_pdf_carries_the_total_the_resources_produce(self):
        """The positive control, and it comes first deliberately: it proves
        extraction reaches the figures on this document, so the absences
        asserted below mean something."""
        self.assertEqual(self.sheet.sow_total, Decimal('35000'))
        self.assertIn('35,000', self._pdf_text())

    def test_the_resource_description_is_not_in_the_pdf(self):
        """The client sees the service, not the trades behind it. Leaking a
        manpower line onto a quote is a commercial problem, and nothing about
        the page would look wrong."""
        self.assertNotIn('Aramco Approved Splicer', self._pdf_text())

    def test_the_section_itself_is_not_in_the_pdf(self):
        text = self._pdf_text()
        self.assertNotIn('A.4', text)
        self.assertNotIn('Resources', text)


class ResourceEndpointTests(TestCase):
    """Adding, editing and removing lines."""

    def setUp(self):
        for name, _label in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        self.user = User.objects.create_user(
            'a4-edit', password='x', role=Role.objects.get(name=Role.SUPER_ADMIN))
        self.outsider = User.objects.create_user(
            'a4-out', password='x',
            role=Role.objects.get(name=Role.DOCUMENT_CONTROLLER))
        self.sheet = CostingSheet.objects.create(title='Ghazlan',
                                                 output_currency='SAR')
        self.catalogue = ResourceCatalogueItem.objects.get(name='Welder')

    def _add(self, **over):
        # One combo cell per row, so the resource arrives as text and the
        # catalogue link is resolved from it — there is no separate picker to
        # post an id from.
        data = {'description': self.catalogue.name, 'quantity': '2',
                'rate': '500'}
        data.update(over)
        return self.client.post(
            reverse('costing:add_resource_line', args=[self.sheet.pk]), data)

    def test_a_line_can_be_added_by_naming_a_catalogue_resource(self):
        self.client.force_login(self.user)
        response = self._add()
        self.assertEqual(response.status_code, 200)
        line = self.sheet.resource_lines.get()
        self.assertEqual(line.description, 'Welder')
        self.assertEqual(line.total_price, Decimal('1000'))

    def test_naming_a_catalogue_resource_links_it(self):
        """The link is what makes the catalogue reportable, and it has to
        survive being typed rather than picked."""
        self.client.force_login(self.user)
        self._add()
        self.assertEqual(self.sheet.resource_lines.get().catalogue_item,
                         self.catalogue)

    def test_the_match_ignores_case(self):
        """Typing is the point of a combo box; 'welder' is the same resource
        as 'Welder'."""
        self.client.force_login(self.user)
        self._add(description='welder')
        self.assertEqual(self.sheet.resource_lines.get().catalogue_item,
                         self.catalogue)

    def test_the_description_is_snapshotted_not_followed(self):
        """Renaming a catalogue entry must not rewrite what a quoted sheet
        says it was priced on."""
        self.client.force_login(self.user)
        self._add()
        self.catalogue.name = 'Welder (6G certified)'
        self.catalogue.save()
        self.assertEqual(self.sheet.resource_lines.get().description, 'Welder')

    def test_a_one_off_can_be_typed_without_the_catalogue(self):
        self.client.force_login(self.user)
        self._add(description='Crane hire')
        line = self.sheet.resource_lines.get()
        self.assertEqual(line.description, 'Crane hire')
        self.assertIsNone(line.catalogue_item)

    def test_editing_a_row_into_a_catalogue_name_links_it(self):
        """A row typed as free text and later corrected to a real resource
        should stop being a one-off."""
        self.client.force_login(self.user)
        self._add(description='Weldr')
        line = self.sheet.resource_lines.get()
        self.assertIsNone(line.catalogue_item)
        self.client.post(reverse('costing:update_resource_line', args=[line.pk]),
                         {'description': 'Welder', 'quantity': '2', 'rate': '500'})
        line.refresh_from_db()
        self.assertEqual(line.catalogue_item, self.catalogue)

    def test_a_line_with_no_resource_at_all_is_refused(self):
        self.client.force_login(self.user)
        response = self._add(description='')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.sheet.resource_lines.exists())

    def test_a_negative_rate_is_refused(self):
        """It would quietly reduce the contract total — a correction belongs
        on the line it concerns, not buried in the resources."""
        self.client.force_login(self.user)
        response = self._add(rate='-500')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.sheet.resource_lines.exists())

    def test_the_response_carries_the_totals_that_moved(self):
        self.client.force_login(self.user)
        payload = self._add().json()
        self.assertEqual(payload['resources_total'], '1000.00')
        self.assertEqual(payload['sow_total'], '1000.00')

    def test_editing_a_line_moves_the_totals(self):
        self.client.force_login(self.user)
        self._add()
        line = self.sheet.resource_lines.get()
        response = self.client.post(
            reverse('costing:update_resource_line', args=[line.pk]),
            {'description': 'Welder', 'quantity': '3', 'rate': '500'})
        self.assertEqual(response.json()['sow_total'], '1500.00')

    def test_deleting_a_line_removes_it(self):
        self.client.force_login(self.user)
        self._add()
        line = self.sheet.resource_lines.get()
        self.client.post(reverse('costing:delete_resource_line', args=[line.pk]))
        self.assertFalse(self.sheet.resource_lines.exists())

    def test_somebody_who_cannot_edit_the_sheet_cannot_add(self):
        self.client.force_login(self.outsider)
        response = self._add()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.sheet.resource_lines.exists())

    def test_somebody_who_cannot_edit_the_sheet_cannot_delete(self):
        self.client.force_login(self.user)
        self._add()
        line = self.sheet.resource_lines.get()
        self.client.force_login(self.outsider)
        self.client.post(reverse('costing:delete_resource_line', args=[line.pk]))
        self.assertTrue(self.sheet.resource_lines.exists())
