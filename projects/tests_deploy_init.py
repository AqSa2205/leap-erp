"""A redeploy must preserve what people changed. Audit F07.

build.sh runs on every deploy. It used to replay two fixtures with explicit
primary keys, which overwrite rows outright, and to reset the bootstrap admin
account. These tests are the audit's acceptance check made permanent: change
seeded records the way an admin would, run the deploy initialisation twice,
and require the changes to survive.
"""

import re
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from projects.models import ProjectStatus, Region

BUILD_SH = Path(settings.BASE_DIR) / 'build.sh'


def deploy_init():
    """The seeding step build.sh runs on every deploy."""
    call_command('load_initial_data', stdout=StringIO())


class RedeployPreservesEditsTests(TestCase):

    def setUp(self):
        # A table that has been through at least one deploy.
        deploy_init()

    def test_a_renamed_region_survives_two_redeploys(self):
        region = Region.objects.get(code='LNA')
        region.name = 'Leap Networks Arabia (renamed)'
        region.dashboard_group = 'GCC'
        region.save()
        deploy_init()
        deploy_init()
        region.refresh_from_db()
        self.assertEqual(region.name, 'Leap Networks Arabia (renamed)')
        self.assertEqual(region.dashboard_group, 'GCC')

    def test_a_deleted_region_is_not_resurrected(self):
        """Regions can be deleted from the ERP since #237. Seeding by code
        would put a deleted one straight back on the next deploy."""
        Region.objects.filter(code='PA').delete()
        deploy_init()
        self.assertFalse(Region.objects.filter(code='PA').exists())

    def test_a_recoded_region_is_not_duplicated(self):
        region = Region.objects.get(code='UK')
        region.code = 'GB'
        region.save()
        before = Region.objects.count()
        deploy_init()
        self.assertEqual(Region.objects.count(), before)
        self.assertFalse(Region.objects.filter(code='UK').exists())

    def test_a_renamed_status_is_not_recreated_beside_its_new_name(self):
        status = ProjectStatus.objects.get(name='IP')
        status.name = 'In Progress'
        status.save()
        before = ProjectStatus.objects.count()
        deploy_init()
        self.assertEqual(ProjectStatus.objects.count(), before)
        self.assertFalse(ProjectStatus.objects.filter(name='IP').exists())

    def test_the_won_tile_flag_survives_a_redeploy(self):
        """The flag #223 introduced. The fixture replay reset it to its
        default on every deploy."""
        status = ProjectStatus.objects.get(name='Won')
        status.excluded_from_won_tile = True
        status.save()
        deploy_init()
        status.refresh_from_db()
        self.assertTrue(status.excluded_from_won_tile)

    def test_an_empty_database_is_still_seeded(self):
        """Bootstrap-only must still bootstrap."""
        Region.objects.all().delete()
        ProjectStatus.objects.all().delete()
        deploy_init()
        self.assertEqual(Region.objects.count(), 4)
        self.assertEqual(ProjectStatus.objects.count(), 10)


class BuildScriptTests(TestCase):
    """build.sh cannot be run in a test, so its shape is asserted instead.
    Each of these would otherwise be undone by someone restoring an old
    version of the script without knowing why it changed."""

    def setUp(self):
        self.script = BUILD_SH.read_text(encoding='utf-8')
        # Comment lines explain what was removed; only commands count.
        self.commands = '\n'.join(
            line for line in self.script.splitlines()
            if not line.lstrip().startswith('#'))

    def test_no_fixture_is_loaded_on_deploy(self):
        """Both fixtures carry explicit primary keys, and loaddata overwrites
        a matching row outright."""
        self.assertNotRegex(self.commands, r'manage\.py\s+loaddata')

    def test_initial_seeding_failure_is_not_swallowed(self):
        line = next(l for l in self.commands.splitlines()
                    if 'load_initial_data' in l)
        self.assertNotIn('||', line)

    def test_the_admin_role_is_only_assigned_when_missing(self):
        """Unconditional, it put the bootstrap account back to 'admin' on
        every deploy."""
        block = re.search(r'Assigning admin role.*?Could not assign role',
                          self.commands, re.S)
        self.assertIsNotNone(block)
        self.assertIn('role_id is None', block.group(0))

    def test_sequences_are_still_resynced_last(self):
        """Removing the fixtures must not remove this - a PITR restore also
        leaves the sequences behind."""
        self.assertIn('resync_sequences', self.commands)
