from django.db import transaction
from django.utils import timezone

from .models import TimesheetEntry, TimesheetMonth


def submit_month(*, employee, year, month, submitted_by):
    """Lock an employee's timesheet for one calendar month.

    Mirrors leave_approval_services.py's shape: this is the one authoritative
    place the lock actually happens — the view calls this rather than
    flipping fields itself, so the same rules apply no matter what UI (or
    future HR bulk-action) triggers a submit.
    """
    entries = TimesheetEntry.objects.filter(
        employee=employee, date__year=year, date__month=month)

    if not entries.exists():
        raise ValueError('There are no entries logged for this month yet — nothing to submit.')

    draft_entries = entries.filter(status=TimesheetEntry.STATUS_DRAFT)

    with transaction.atomic():
        tsm, _ = TimesheetMonth.objects.get_or_create(
            employee=employee, year=year, month=month)

        if tsm.is_submitted:
            raise ValueError('This month is already submitted.')

        draft_entries.update(status=TimesheetEntry.STATUS_SUBMITTED)

        tsm.submitted_at = timezone.now()
        tsm.submitted_by = submitted_by
        tsm.save(update_fields=['submitted_at', 'submitted_by'])

    return tsm


def reopen_month(*, employee, year, month, reopened_by):
    """HR-side: unlock a month so the employee can fix a mistake.

    This flips entries back to draft too — a reopened month with entries
    still marked 'submitted' would mean the employee still couldn't edit
    them, defeating the point of reopening.
    """
    try:
        tsm = TimesheetMonth.objects.get(employee=employee, year=year, month=month)
    except TimesheetMonth.DoesNotExist:
        raise ValueError('This month was never submitted, so there is nothing to reopen.')

    if not tsm.is_submitted:
        raise ValueError('This month is not currently submitted.')

    with transaction.atomic():
        TimesheetEntry.objects.filter(
            employee=employee, date__year=year, date__month=month,
            status=TimesheetEntry.STATUS_SUBMITTED,
        ).update(status=TimesheetEntry.STATUS_DRAFT)

        tsm.reopened_at = timezone.now()
        tsm.reopened_by = reopened_by
        tsm.save(update_fields=['reopened_at', 'reopened_by'])

    return tsm