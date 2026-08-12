from datetime import date as date_cls

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import DevTask


@receiver(pre_save, sender=DevTask)
def _stash_previous_status(sender, instance, **kwargs):
    """Capture the status as it currently exists in the DB, before this
    save overwrites it -- post_save needs this to detect an actual
    transition, not just 'the row was saved again with the same status'."""
    if instance.pk:
        try:
            instance._previous_status = DevTask.objects.get(pk=instance.pk).status
        except DevTask.DoesNotExist:
            instance._previous_status = None
    else:
        instance._previous_status = None


@receiver(post_save, sender=DevTask)
def _log_timesheet_entry_on_status_change(sender, instance, created, **kwargs):
    """Auto-creates a draft TimesheetEntry when a task transitions INTO
    in_progress ('Started task: X') or INTO done ('Ended task: X'), dated
    the day the transition actually happens. Catches every path that can
    change status -- mark_started(), mark_done(), direct admin edits --
    since it hooks the model's save(), not any one specific method.
    Silently does nothing if the task has no developer, that developer
    has no linked Employee record, or the fixed activity code is missing
    -- this must never crash a task save over a timesheet side-effect."""
    previous_status = getattr(instance, '_previous_status', None)
    if previous_status == instance.status:
        return
    if instance.developer_id is None:
        return

    employee = getattr(instance.developer, 'employee_profile', None)
    if employee is None:
        return

    from timesheets.models import ActivityCode, TimesheetEntry
    try:
        code = ActivityCode.objects.get(code='COS_0009')
    except ActivityCode.DoesNotExist:
        return

    if instance.status == 'in_progress':
        TimesheetEntry.objects.create(
            employee=employee, date=date_cls.today(), activity_code=code,
            task_description=f'Started task: {instance.title}', hours=10,
        )
    elif instance.status == 'done':
        TimesheetEntry.objects.create(
            employee=employee, date=date_cls.today(), activity_code=code,
            task_description=f'Ended task: {instance.title}', hours=10,
        )