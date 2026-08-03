from django.conf import settings
from django.db import models


class CalendarCell(models.Model):
    """One row = one employee's display cell for one calendar day, in the
    HR-facing Engineer Calendar. This is intentionally NOT just a live query
    over TimesheetEntry/LeaveRecord/Holiday/WFHRecord — it has to remember
    what's currently shown and where it came from, so that:
      - HR's manual corrections survive a later "Generate Draft" re-run
      - if the underlying data changes after a manual correction, the cell
        gets flagged instead of silently overwritten OR silently going stale

    Precedence when auto-generating (matches hr.attendance_matrix.build_matrix,
    the existing convention for this exact kind of leave/holiday/weekend/wfh
    resolution elsewhere in the app):
        leave > holiday > weekend > wfh > timesheet-derived text > blank
    """
    SOURCE_LEAVE = 'leave'
    SOURCE_HOLIDAY = 'holiday'
    SOURCE_WEEKEND = 'weekend'
    SOURCE_WFH = 'wfh'
    SOURCE_TIMESHEET = 'timesheet'   # derived from TimesheetEntry
    SOURCE_MANUAL = 'manual'         # HR typed this by hand — never auto-overwritten
    SOURCE_BLANK = 'blank'           # nothing to show; auto-fill found nothing

    SOURCE_CHOICES = [
        (SOURCE_LEAVE, 'Leave'),
        (SOURCE_HOLIDAY, 'Holiday'),
        (SOURCE_WEEKEND, 'Weekend'),
        (SOURCE_WFH, 'WFH'),
        (SOURCE_TIMESHEET, 'Timesheet'),
        (SOURCE_MANUAL, 'Manual (HR-entered)'),
        (SOURCE_BLANK, 'Blank'),
    ]

    # Sources that are locked from casual auto-overwrite because they reflect
    # a fixed calendar fact (holiday/weekend) rather than something that can
    # drift day to day — matches build_matrix's 'locked' concept for the same
    # two statuses.
    LOCKED_SOURCES = (SOURCE_HOLIDAY, SOURCE_WEEKEND)

    employee = models.ForeignKey(
        'hr.Employee', on_delete=models.CASCADE, related_name='calendar_cells')
    date = models.DateField()

    display_text = models.CharField(
        max_length=255, blank=True,
        help_text='What actually shows in the calendar cell.')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_BLANK)

    # Snapshot of what auto-generation would currently produce for this cell.
    # Only meaningful when source == manual — refreshed on every "Generate
    # Draft" run and compared against display_text to detect drift, without
    # ever touching HR's actual correction.
    auto_generated_text = models.CharField(max_length=255, blank=True)

    needs_review = models.BooleanField(
        default=False,
        help_text='Set by Generate Draft when this cell looks suspicious or '
                   'conflicts with another data source — HR should double check it.')
    review_reason = models.CharField(
        max_length=255, blank=True,
        help_text="Short note on why needs_review was set, e.g. "
                  "'Same description 3+ days running' or "
                  "'Timesheet entry logged on an approved leave day'.")

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('employee', 'date')
        ordering = ['employee', 'date']
        indexes = [models.Index(fields=['date', 'employee'])]

    def __str__(self):
        return f"{self.employee} {self.date} [{self.source}]"

    @property
    def is_locked(self):
        """Matches build_matrix's convention: holiday/weekend cells are
        fixed calendar facts, not something HR corrects day to day."""
        return self.source in self.LOCKED_SOURCES
