"""Who last updated a purchase order.

PurchaseOrder.save() stamps updated_by on exactly the saves that move
updated_at, so "Updated By" and "Updated At" always describe the same save.
The user comes from procurement.request_user, set per request by its
middleware; outside a request there is nobody to record.
"""
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.urls import reverse

from procurement.models import PurchaseOrder
from procurement.request_user import CurrentUserMiddleware, _current_user, current_user
from procurement.tests_stage_signer import a_po, a_super_admin


class UpdatedByTests(TestCase):

    def acting_as(self, user):
        """Stand in for the middleware: this user is making the request."""
        token = _current_user.set(user)
        self.addCleanup(_current_user.reset, token)

    def test_a_save_during_a_request_records_who(self):
        po = a_po('PO-UB-1')
        editor = a_super_admin('editor')
        self.acting_as(editor)
        po.save()
        po.refresh_from_db()
        self.assertEqual(po.updated_by, editor)

    def test_a_save_that_names_updated_at_records_who(self):
        po = a_po('PO-UB-2')
        editor = a_super_admin('editor2')
        self.acting_as(editor)
        po.vendor_name = 'Other'
        po.save(update_fields=['vendor_name', 'updated_at'])
        po.refresh_from_db()
        self.assertEqual(po.updated_by, editor)

    def test_a_save_that_leaves_updated_at_alone_leaves_updated_by_alone(self):
        """The two must describe the same save. A save limited to fields that
        do not include updated_at moves neither."""
        po = a_po('PO-UB-3')
        first = a_super_admin('first')
        self.acting_as(first)
        po.save()
        stamped_at = PurchaseOrder.objects.get(pk=po.pk).updated_at

        self.acting_as(a_super_admin('second'))
        po.vendor_name = 'Other'
        po.save(update_fields=['vendor_name'])
        po.refresh_from_db()
        self.assertEqual(po.updated_by, first)
        self.assertEqual(po.updated_at, stamped_at)

    def test_a_save_outside_a_request_records_nobody(self):
        po = a_po('PO-UB-4')
        po.save()
        po.refresh_from_db()
        self.assertIsNone(po.updated_by)

    def test_an_anonymous_request_records_nobody(self):
        self.acting_as(AnonymousUser())
        po = a_po('PO-UB-5')
        po.save()
        po.refresh_from_db()
        self.assertIsNone(po.updated_by)

    def test_the_middleware_exposes_the_user_only_during_the_request(self):
        user = a_super_admin('mw')
        seen = []

        def view(request):
            seen.append(current_user())
            return 'ok'

        request = RequestFactory().get('/')
        request.user = user
        CurrentUserMiddleware(view)(request)
        self.assertEqual(seen, [user])
        self.assertIsNone(current_user())

    def test_the_middleware_runs_after_authentication(self):
        """It reads request.user, which AuthenticationMiddleware sets."""
        mw = list(settings.MIDDLEWARE)
        ours = 'procurement.request_user.CurrentUserMiddleware'
        self.assertIn(ours, mw)
        self.assertGreater(mw.index(ours),
                           mw.index('django.contrib.auth.middleware.AuthenticationMiddleware'))


class UpdatedByOnThePageTests(TestCase):

    def test_the_detail_page_names_who_last_updated(self):
        editor = a_super_admin('ed')
        editor.first_name, editor.last_name = 'Omar', 'Haddad'
        editor.save()
        po = a_po('PO-UB-PAGE')
        token = _current_user.set(editor)
        try:
            po.save()
        finally:
            _current_user.reset(token)

        self.client.force_login(a_super_admin('viewer'))
        resp = self.client.get(reverse('procurement:po_detail', args=[po.pk]))
        self.assertContains(resp, 'Updated By')
        self.assertContains(resp, 'Omar Haddad')

    def test_a_po_nobody_has_updated_still_renders(self):
        """Every PO saved before this existed has no updated_by. The page
        must say so plainly, not fail."""
        po = a_po('PO-UB-NOBODY')
        self.client.force_login(a_super_admin('viewer2'))
        resp = self.client.get(reverse('procurement:po_detail', args=[po.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Updated By')
