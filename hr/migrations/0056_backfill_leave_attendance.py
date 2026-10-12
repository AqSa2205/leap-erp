"""Backfill: stored attendance rows on days covered by an approved leave.

Before this branch, approving a leave created the LeaveRecord but never
touched attendance rows already saved for those days, so a sick day recorded
as 'absent' stayed 'absent' on the register after the leave was approved.
Approval/revoke/delete now keep the rows in step (sync_attendence_with_leave);
this fixes the backlog on deploy, since production has no shell to run a
one-off script from.

Why it is written this way:
- On a day covered by a LeaveRecord, derive_status always returns
  ('leave', None) - leave outranks everything - so the backfill is simply
  "set those rows to leave", done with historical models rather than
  importing live code into a migration.
- check_in/check_out are left untouched, so a revoke can still restore the
  day as it really was.
- queryset.update() skips AttendanceRecord.save(), so no late-warning email
  can fire. That is also why it is safe: rows only ever move TO 'leave',
  never to 'late'. Do not widen this beyond leave ranges without revisiting
  that - re-deriving other days through save() would email people about
  historical lates.
- Idempotent: rows already at ('leave', None) are excluded, so re-running
  changes nothing.
"""
from django.db import migrations


def mark_leave_days(apps, schema_editor):
    LeaveRecord = apps.get_model('hr', 'LeaveRecord')
    AttendanceRecord = apps.get_model('hr', 'AttendanceRecord')
    for lr in LeaveRecord.objects.all().only('employee_id', 'start_date', 'end_date').iterator():
        (AttendanceRecord.objects
         .filter(employee_id=lr.employee_id, date__range=(lr.start_date, lr.end_date))
         .exclude(status='leave', hours_worked__isnull=True)
         .update(status='leave', hours_worked=None))


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0055_leaverequest_replacement'),
    ]

    operations = [
        # No reverse: the statuses the rows held before can't be recovered,
        # and they were wrong anyway. Rolling back leaves the rows as 'leave'.
        migrations.RunPython(mark_leave_days, migrations.RunPython.noop),
    ]
