"""Generate Draft: builds/refreshes CalendarCell rows for a set of employees
across one month, from TimesheetEntry + the same leave/holiday/weekend/WFH
resolution hr.attendance_matrix.build_matrix() already uses for the
Attendance Register — so the two screens never disagree about what a given
day is.

Never overwrites a manually-entered cell's display_text (see CalendarCell's
docstring). Manual cells instead get their auto_generated_text snapshot
refreshed, and get flagged for review if that snapshot changed.
"""
import calendar
from collections import defaultdict
from datetime import date as date_cls

from django.db import transaction

from hr.attendance_matrix import build_matrix
from timesheets.models import TimesheetEntry
from .models import CalendarCell


SUSPICIOUS_RUN_LENGTH = 3  # same auto-derived description this many days running -> flag

STATUS_TEXT = {'leave': 'Leave', 'holiday': 'Holiday', 'weekend': 'Weekend', 'wfh': 'WFH'}
STATUS_SOURCE = {
    'leave': CalendarCell.SOURCE_LEAVE,
    'holiday': CalendarCell.SOURCE_HOLIDAY,
    'weekend': CalendarCell.SOURCE_WEEKEND,
    'wfh': CalendarCell.SOURCE_WFH,
}


def _entry_label(entry):
    """Same convention as timesheets.views._entry_label — project reference
    or activity code, paired with the task description. Matches the format
    HR's real spreadsheet already uses (e.g. 'PO2308 Amiral Project
    Execution Activity')."""
    code = entry.project.proposal_reference if entry.project_id else entry.activity_code.code
    return f"{code} {entry.task_description}".strip()


def _timesheet_text_for_day(entries):
    """Combine a day's TimesheetEntry rows into one cell string. Distinct
    entries the same day are joined with ' + ', mirroring how the real
    master calendar already combines same-day work (e.g. 'C000_9 & P02575
    Head Office & Maintenance of SRMS')."""
    return ' + '.join(_entry_label(e) for e in entries)


def generate_draft(employees, year, month, updated_by=None):
    """Refresh CalendarCell rows for the given employees across one month.
    Returns the number of cells created or updated."""
    days_in_month = calendar.monthrange(year, month)[1]
    start = date_cls(year, month, 1)
    end = date_cls(year, month, days_in_month)

    days, matrix_rows = build_matrix(employees, start, end)
    status_by_emp_day = {
        (row['employee'].pk, cell['date']): cell['status']
        for row in matrix_rows for cell in row['cells']
    }

    emp_ids = [e.pk for e in employees]
    entries_by_emp_day = defaultdict(list)
    for e in TimesheetEntry.objects.filter(
            employee_id__in=emp_ids, date__range=(start, end)
        ).select_related('activity_code', 'project').order_by('id'):
        entries_by_emp_day[(e.employee_id, e.date)].append(e)

    existing_cells = {
        (c.employee_id, c.date): c
        for c in CalendarCell.objects.filter(employee_id__in=emp_ids, date__range=(start, end))
    }

    to_create = []
    changed_count = 0

    for emp in employees:
        run_text, run_length = None, 0

        for day in days:
            key = (emp.pk, day)
            matrix_status = status_by_emp_day.get(key, '')
            day_entries = entries_by_emp_day.get(key, [])

            if matrix_status in STATUS_SOURCE:
                auto_source = STATUS_SOURCE[matrix_status]
                auto_text = STATUS_TEXT[matrix_status]
            elif day_entries:
                auto_source = CalendarCell.SOURCE_TIMESHEET
                auto_text = _timesheet_text_for_day(day_entries)
            else:
                auto_source = CalendarCell.SOURCE_BLANK
                auto_text = ''

            needs_review, review_reason = False, ''

            # A real timesheet entry on a leave/holiday/WFH day is always a
            # conflict worth a look, regardless of description length.
            if matrix_status in ('leave', 'holiday', 'wfh') and day_entries:
                needs_review = True
                review_reason = f'Timesheet entry logged on a {STATUS_TEXT[matrix_status].lower()} day.'

            # Repeated identical description, 3+ days running — only tracked
            # for genuine timesheet text; leave/holiday/weekend/WFH repeating
            # is expected, not lazy.
            if auto_source == CalendarCell.SOURCE_TIMESHEET and auto_text:
                run_length = run_length + 1 if auto_text == run_text else 1
                run_text = auto_text
                if run_length >= SUSPICIOUS_RUN_LENGTH:
                    needs_review = True
                    review_reason = f'Same description {run_length}+ days running — check for a copy-pasted entry.'
            else:
                run_text, run_length = None, 0

            existing = existing_cells.get(key)

            if existing is None:
                to_create.append(CalendarCell(
                    employee=emp, date=day, display_text=auto_text, source=auto_source,
                    auto_generated_text=auto_text, needs_review=needs_review,
                    review_reason=review_reason, updated_by=updated_by,
                ))
                changed_count += 1
                continue

            if existing.source == CalendarCell.SOURCE_MANUAL:
                # display_text is HR's — never touched here. Only the
                # snapshot moves, and only refresh needs_review on drift.
                if existing.auto_generated_text != auto_text:
                    existing.auto_generated_text = auto_text
                    existing.needs_review = True
                    existing.review_reason = 'Underlying timesheet data changed since this cell was corrected.'
                    existing.updated_by = updated_by
                    existing.save()
                    changed_count += 1
                continue

            existing.display_text = auto_text
            existing.source = auto_source
            existing.auto_generated_text = auto_text
            existing.needs_review = needs_review
            existing.review_reason = review_reason
            existing.updated_by = updated_by
            existing.save()
            changed_count += 1

    if to_create:
        with transaction.atomic():
            CalendarCell.objects.bulk_create(to_create)

    return changed_count