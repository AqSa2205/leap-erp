"""User.email uniqueness, and the mailbox-assignment check it backs up.

Both exist because of how app-only Mail.Read works: it reads whichever
mailbox address it is handed, so an address on two accounts is a colleague's
inbox reachable from the wrong one.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User


class UniqueEmailTests(TestCase):

    def test_two_accounts_cannot_share_an_address(self):
        User.objects.create_user('a', password='x', email='same@leap-arabia.com')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user('b', password='x',
                                         email='same@leap-arabia.com')

    def test_a_case_variant_is_the_same_address(self):
        """A plain unique index is case-SENSITIVE, so ceo@ and CEO@ would both
        satisfy it - while being one mailbox to Graph and matching each other
        under the email__iexact the auth backend uses."""
        User.objects.create_user('lower', password='x',
                                 email='ceo@leap-arabia.com')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user('upper', password='x',
                                         email='CEO@leap-arabia.com')

    def test_a_case_variant_cannot_be_slipped_in_by_update(self):
        User.objects.create_user('a', password='x', email='x@leap-arabia.com')
        other = User.objects.create_user('b', password='x',
                                         email='y@leap-arabia.com')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.filter(pk=other.pk).update(
                    email='X@LEAP-ARABIA.COM')

    def test_many_accounts_may_have_no_address(self):
        """Most accounts have none. The constraint exempts blanks, or the
        second one would collide."""
        for i in range(5):
            User.objects.create_user(f'blank{i}', password='x')
        self.assertEqual(User.objects.filter(email='').count(), 5)

    def test_a_blank_address_stays_an_empty_string(self):
        """Not NULL. Every consumer, template and PDF treats '' as "no
        email"; NULL renders as the literal text "None" and breaks string
        handling in the PDF builders."""
        user = User.objects.create_user('blank', password='x', email='')
        user.refresh_from_db()
        self.assertEqual(user.email, '')

    def test_clearing_an_address_frees_it_for_another_account(self):
        first = User.objects.create_user('first', password='x',
                                         email='shared@leap-arabia.com')
        first.email = ''
        first.save()
        second = User.objects.create_user('second', password='x',
                                          email='shared@leap-arabia.com')
        self.assertEqual(second.email, 'shared@leap-arabia.com')

    def test_login_by_email_is_unambiguous(self):
        from django.contrib.auth import authenticate
        User.objects.create_user('only', password='secret-pw',
                                 email='one@leap-arabia.com')
        user = authenticate(username='one@leap-arabia.com',
                            password='secret-pw')
        self.assertIsNotNone(user)
        self.assertEqual(user.username, 'only')


class MigrationDedupeLogicTests(TestCase):
    """The migration's dedupe rule, as a pure function.

    Once the constraint exists a duplicate cannot be created in a test
    database even with .update(), so this branch would otherwise run for the
    first time on production - where there is no shell to fix it if the rule
    is wrong.
    """

    def setUp(self):
        from importlib import import_module
        self.rule = import_module(
            'accounts.migrations.0036_unique_user_email').duplicates_to_clear

    def test_lowest_pk_keeps_the_address(self):
        """The same precedence accounts/backends.py already applies, so
        nobody's ability to sign in changes."""
        cleared = self.rule([(1, 'first', 'dup@leap-arabia.com'),
                             (2, 'second', 'dup@leap-arabia.com')])
        self.assertEqual([row[0] for row in cleared], [2])
        self.assertEqual(cleared[0][3], 'first')

    def test_case_differences_are_the_same_address(self):
        cleared = self.rule([(1, 'lower', 'mix@leap-arabia.com'),
                             (2, 'upper', 'MIX@leap-arabia.com')])
        self.assertEqual([row[0] for row in cleared], [2])

    def test_surrounding_whitespace_is_the_same_address(self):
        cleared = self.rule([(1, 'a', 'x@leap-arabia.com'),
                             (2, 'b', '  x@leap-arabia.com  ')])
        self.assertEqual([row[0] for row in cleared], [2])

    def test_distinct_addresses_are_all_kept(self):
        self.assertEqual(self.rule([(1, 'a', 'a@x.com'),
                                    (2, 'b', 'b@x.com')]), [])

    def test_blanks_are_not_duplicates_of_each_other(self):
        self.assertEqual(self.rule([(1, 'a', ''), (2, 'b', ''),
                                    (3, 'c', None)]), [])

    def test_three_sharing_one_address_leaves_only_the_first(self):
        cleared = self.rule([(1, 'a', 's@x.com'), (2, 'b', 's@x.com'),
                             (3, 'c', 's@x.com')])
        self.assertEqual([row[0] for row in cleared], [2, 3])
