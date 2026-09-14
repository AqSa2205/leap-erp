"""Reconcile the mailbox-setup branch with dev.

Two chains were cut from projects/0017 and both reached 0021:

    0018_pipelineemail_source_mailbox_monitoredmailbox  ->  0020_merge  ->
        0021_monitoredmailbox_assigned_by_and_more      (this branch)
    0018_region_dashboard_group -> 0019_alter_project_project_stage ->
        0020_projectstatus_excluded_from_won_tile -> 0021_project_location  (dev)

Django reads the dependencies, not the filename, so two migrations with no
path between them are two leaf nodes however they are numbered - and migrate
refuses to run at all. Git reported this branch as mergeable throughout,
because the colliding files have different names.

The branch already carries 0020_merge, which reconciled the same fork against
dev as it stood on 10 September. Dev has since gained 0020 and 0021 (#223 and
#212), so that merge no longer reaches the tip and this one is needed.

No operations: a merge migration only joins the graph.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0021_monitoredmailbox_assigned_by_and_more'),
        ('projects', '0021_project_location'),
    ]

    operations = [
    ]
