"""One account per email address, case-insensitively, blanks exempt.

Why it is needed, both live today:

* Login accepts username-or-email. accounts/backends.py resolves a duplicate
  with order_by('id').first(), so of two accounts sharing an address only the
  lower id can ever sign in by email, and nothing tells the other person why.
* A mailbox is read from User.email through app-only Mail.Read, which reads
  whichever mailbox it is handed. Two accounts claiming one address is the
  shape of a colleague's inbox reachable from the wrong account.

Why it is shaped this way:

* Lower('email') - a plain unique index treats ceo@x.com and CEO@x.com as
  different values, while Graph and email__iexact treat them as one.
* condition email != '' - most accounts have no address, and '' is a value;
  an unconditional index makes the second blank collide. Blanks stay '' rather
  than becoming NULL because every consumer, template and PDF in the app
  treats '' as "no email", and NULL renders as the text "None".

Production has no shell, so a migration that raises leaves a half-deployed
site with no way in. Duplicates are therefore resolved rather than refused:
the lowest id keeps the address - exactly who the auth backend already let
log in with it, so nobody's ability to sign in changes - and the rest are
cleared to '' and named in the deploy log so an admin can set the right one.
"""

from django.db import migrations, models
from django.db.models.functions import Lower


def duplicates_to_clear(rows):
    """Which rows lose their address, given (pk, username, email) tuples.

    Pure so it can be tested: once the constraint exists, a duplicate cannot
    be created in a test database at all, so this branch would otherwise run
    for the first time on production.
    """
    seen = {}
    cleared = []
    for pk, username, email in rows:
        key = (email or '').strip().lower()
        if not key:
            continue
        if key in seen:
            cleared.append((pk, username, email, seen[key]))
        else:
            seen[key] = username
    return cleared


def dedupe(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    rows = list(User.objects.exclude(email='').order_by('id')
                .values_list('pk', 'username', 'email'))
    cleared = duplicates_to_clear(rows)
    if not cleared:
        return
    User.objects.filter(pk__in=[pk for pk, _u, _e, _k in cleared]).update(email='')
    print(f'  email: {len(cleared)} duplicate address(es) cleared - these users '
          f'can still sign in by username, but have no address on file until '
          f'one is set under Administration -> Users:')
    for _pk, username, address, kept_by in cleared:
        print(f'    - {username} had {address} (kept by {kept_by})')


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0035_grant_pcc_costing_access'),
    ]

    operations = [
        # Data first: the constraint cannot go on while duplicates exist.
        # Not reversible for the cleared rows - the address they held now
        # belongs to another account, and restoring it would recreate the
        # ambiguity this removes.
        migrations.RunPython(dedupe, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(
                Lower('email'),
                condition=models.Q(('email', ''), _negated=True),
                name='unique_user_email_ci_nonblank',
            ),
        ),
    ]
