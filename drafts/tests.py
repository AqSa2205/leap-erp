import json

from django.test import TestCase
from django.urls import reverse

from accounts.models import User, Role
from drafts.models import FormDraft


class DraftsSecurityTests(TestCase):
    """A server-side draft store holds half-typed form data, so the two things
    that must never break are: drafts are scoped to their owner, and the write
    endpoint is CSRF-protected."""

    def setUp(self):
        self.role, _ = Role.objects.get_or_create(name=Role.SALES_REP)
        self.alice = User.objects.create_user('alice', password='pw', role=self.role)
        self.bob = User.objects.create_user('bob', password='pw', role=self.role)
        self.bobs_draft = FormDraft.objects.create(
            user=self.bob, form_key='project_create', object_id=None,
            data={'project_name': "bob's secret bid"})

    # ── ownership ────────────────────────────────────────────────
    def test_user_cannot_see_another_users_drafts(self):
        self.client.force_login(self.alice)
        r = self.client.get(reverse('drafts:check'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['count'], 0)          # Bob's draft is invisible
        self.assertNotIn("bob's secret bid", r.content.decode())

    def test_user_cannot_discard_another_users_draft(self):
        self.client.force_login(self.alice)
        r = self.client.post(
            reverse('drafts:discard', kwargs={'pk': self.bobs_draft.pk}))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(FormDraft.objects.filter(pk=self.bobs_draft.pk).exists())

    def test_owner_can_discard_own_draft(self):
        self.client.force_login(self.bob)
        r = self.client.post(
            reverse('drafts:discard', kwargs={'pk': self.bobs_draft.pk}))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(FormDraft.objects.filter(pk=self.bobs_draft.pk).exists())

    def test_anonymous_is_redirected_to_login(self):
        self.assertEqual(self.client.get(reverse('drafts:check')).status_code, 302)

    # ── CSRF ─────────────────────────────────────────────────────
    def _json_post(self, payload, **extra):
        return self.client.post(
            reverse('drafts:save'), data=json.dumps(payload),
            content_type='application/json', **extra)

    def test_save_without_csrf_token_is_rejected(self):
        # enforce_csrf_checks mirrors production. Previously this view was
        # @csrf_exempt with a hand-rolled check that PASSED when both the
        # header and the cookie were absent.
        c = self.client_class(enforce_csrf_checks=True)
        c.force_login(self.alice)
        r = c.post(reverse('drafts:save'),
                   data=json.dumps({'form_key': 'project_create', 'data': {}}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 403)

    def test_save_with_csrf_header_succeeds(self):
        c = self.client_class(enforce_csrf_checks=True)
        c.force_login(self.alice)
        c.get(reverse('projects:create'))                 # sets the csrf cookie
        token = c.cookies['csrftoken'].value
        r = c.post(reverse('drafts:save'),
                   data=json.dumps({'form_key': 'project_create',
                                    'data': {'project_name': 'Draft A'}}),
                   content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(FormDraft.objects.filter(user=self.alice).exists())

    def test_beacon_form_post_with_csrf_field_succeeds(self):
        """sendBeacon can't set headers, so it posts csrfmiddlewaretoken as a
        form field alongside the JSON payload."""
        c = self.client_class(enforce_csrf_checks=True)
        c.force_login(self.alice)
        c.get(reverse('projects:create'))
        token = c.cookies['csrftoken'].value
        r = c.post(reverse('drafts:save'), data={
            'csrfmiddlewaretoken': token,
            'payload': json.dumps({'form_key': 'project_create',
                                   'data': {'project_name': 'Beacon draft'}}),
        })
        self.assertEqual(r.status_code, 200)
        draft = FormDraft.objects.get(user=self.alice)
        self.assertEqual(draft.data['project_name'], 'Beacon draft')

    # ── input validation ─────────────────────────────────────────
    def test_unknown_form_key_is_rejected(self):
        self.client.force_login(self.alice)
        r = self._json_post({'form_key': 'not_a_real_form', 'data': {}})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(FormDraft.objects.filter(user=self.alice).exists())

    def test_fields_not_on_the_form_are_stripped(self):
        self.client.force_login(self.alice)
        r = self._json_post({'form_key': 'project_create', 'data': {
            'project_name': 'Legit', 'is_superuser': True, 'junk': 'x' * 10,
        }})
        self.assertEqual(r.status_code, 200)
        data = FormDraft.objects.get(user=self.alice).data
        self.assertEqual(data.get('project_name'), 'Legit')
        self.assertNotIn('is_superuser', data)   # allowlisted against the form
        self.assertNotIn('junk', data)

    def test_oversized_payload_is_rejected(self):
        self.client.force_login(self.alice)
        r = self._json_post({'form_key': 'project_create',
                             'data': {'project_name': 'x' * 60000}})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(FormDraft.objects.filter(user=self.alice).exists())

    def test_malformed_body_is_rejected(self):
        self.client.force_login(self.alice)
        r = self.client.post(reverse('drafts:save'), data='not json{',
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_saving_twice_updates_rather_than_duplicates(self):
        self.client.force_login(self.alice)
        self._json_post({'form_key': 'project_create', 'data': {'project_name': 'v1'}})
        self._json_post({'form_key': 'project_create', 'data': {'project_name': 'v2'}})
        drafts = FormDraft.objects.filter(user=self.alice, form_key='project_create')
        self.assertEqual(drafts.count(), 1)
        self.assertEqual(drafts.first().data['project_name'], 'v2')
