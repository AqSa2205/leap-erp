"""Month-end lateness-to-absence consequence report. Every 3 late arrivals
in a month convert to 1 day of absence under company policy - this module
generates a per-employee summary of that pattern and emails it to anyone
with at least one late arrival that month. Purely a record and
notification: never touches AttendanceRecord rows or any existing
absence/leave totals, and late_dates is snapshotted at generation time
rather than derived live, so the report reflects what was true when it
ran even if underlying records are corrected afterward.

Emails are sent synchronously. Callers are the management command (see
hr/management/commands/generate_monthly_lateness_reports.py) and the
Generate button on the Team Exceptions page, which goes through
generate_for_completed_month below - production has no shell or cron, so
that button is the route there. A background thread would be silently
killed before SMTP delivery completes.
That is not hypothetical: it was the original implementation, and it
meant essentially no email was ever actually delivered in production
despite report rows and logs looking correct.

Cron scheduling constraint: is_last_day_of_month is evaluated against
Asia/Riyadh local time (UTC+3), but Render cron schedules run in UTC.
Local midnight is 21:00 UTC the prior day, so the job must be scheduled
strictly before 21:00 UTC to still see "today" as the last day locally;
scheduling at or after 21:00 UTC means the job silently no-ops every
month (today is already the 1st locally). Late arrivals are also frozen
at whatever point in the day the job runs (see generate_monthly_lateness_reports's
docstring), so schedule as close to 21:00 UTC as safely possible - e.g.
20:30 UTC (23:30 Riyadh) - to catch the fullest possible day while
staying inside the safe window.
"""
import calendar
import logging
from datetime import date as date_cls, timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def is_last_day_of_month(d):
    """True if d is the final calendar day of its month - the trigger
    condition generate_monthly_lateness_reports checks, so the
    underlying management command can safely run daily via cron and
    only actually do anything once a month. See this module's docstring
    for the UTC-cron-vs-local-date scheduling constraint."""
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.day == last_day


def generate_monthly_lateness_reports(today=None, _skip_last_day_check=False, target_month=None):
    """For every ACTIVE employee with at least one undisputed 'late'
    AttendanceRecord in the target month, create a MonthlyLatenessReport
    snapshot and email them the summary. Report creation is idempotent
    per (employee, month) via get_or_create - re-running never
    duplicates or overwrites an existing report's snapshot, which also
    means a run partway through the last day permanently freezes
    whatever late arrivals were stamped by that point - schedule cron
    as late in the day as the UTC constraint (see module docstring)
    safely allows. The email send retries on every call until it
    succeeds (report.email_sent_at is None) - a transient failure
    (network blip, mail service down) on one run doesn't silently mean
    the employee never hears about it; once sent, it's never resent.
    Returns the number of NEW reports created this call (retried sends
    on already-existing reports don't add to this count).

    Days with a currently-pending LateQuery are excluded from the count
    entirely (not just flagged) - a disputed lateness that hasn't been
    resolved yet shouldn't be reported to the employee or HR as a
    confirmed policy consequence. If the query is later approved and the
    day flips to Present, the report for that month (if already
    generated) is not retroactively corrected - this is the same
    frozen-snapshot trade-off documented above.

    Refuses to run (returns 0, logs a warning) unless `today` is
    genuinely the last day of its month - a partial-month report would
    otherwise be permanently locked in by the idempotency above, since
    a later correct run on the true last day won't regenerate it. This
    mirrors the management command's own gate, but belongs here too so
    any direct caller (a script, a shell session, a future admin
    action) gets the same protection. Pass _skip_last_day_check=True
    only for a deliberate manual recovery (e.g. the scheduled run was
    missed) - never for routine use.

    target_month (a date; only its year/month are used) lets a recovery
    call explicitly target the missed month instead of deriving it from
    `today`. Without this, recovering a missed 31 Aug run by calling
    this on 1 Sep with _skip_last_day_check=True would generate a
    one-day September report instead of the intended August one,
    permanently locking September to a broken partial snapshot - always
    pass target_month explicitly alongside _skip_last_day_check for any
    manual recovery."""
    from hr.models import AttendanceRecord, MonthlyLatenessReport, LateQuery

    today = today or timezone.localtime(timezone.now()).date()
    if not _skip_last_day_check and not is_last_day_of_month(today):
        logger.warning(
            'generate_monthly_lateness_reports called with %s, which is not '
            'the last day of its month - refusing to run (a partial-month '
            'report would be permanently locked in). Pass '
            '_skip_last_day_check=True and an explicit target_month for a '
            'deliberate manual recovery.', today)
        return 0

    month_start = (target_month or today).replace(day=1)
    last_day_num = calendar.monthrange(month_start.year, month_start.month)[1]
    month_end = date_cls(month_start.year, month_start.month, last_day_num)
    # Bounding by month_end (not just today) matters for a target_month
    # recovery on a later date - without it, a call on 5 Sep recovering
    # August would incorrectly pull in September's lates too.
    range_end = min(month_end, today)

    disputed_record_ids = set(
        LateQuery.objects.filter(status='pending').values_list('attendance_record_id', flat=True)
    )

    late_records = (
        AttendanceRecord.objects.filter(
            status='late', date__gte=month_start, date__lte=range_end,
            employee__is_active=True)
        .exclude(pk__in=disputed_record_ids)
        .select_related('employee', 'employee__user').order_by('employee_id', 'date')
    )
    by_employee = {}
    for r in late_records:
        by_employee.setdefault(r.employee_id, []).append(r)

    created_count = 0
    for employee_id, records in by_employee.items():
        employee = records[0].employee
        total_lates = len(records)
        late_dates = [r.date.isoformat() for r in records]
        converted_absences = total_lates // 3

        report, created = MonthlyLatenessReport.objects.get_or_create(
            employee=employee, month=month_start,
            defaults={
                'total_lates': total_lates,
                'late_dates': late_dates,
                'converted_absences': converted_absences,
            },
        )
        if created:
            created_count += 1

        if report.email_sent_at is None and employee.user_id and employee.user.email:
            _send_lateness_report_email(report)

    return created_count


def _format_late_dates(late_dates):
    """['2026-08-03', '2026-08-05', ...] -> '3 Aug, 5 Aug'"""
    formatted = []
    for iso_date in late_dates:
        d = date_cls.fromisoformat(iso_date)
        formatted.append(f"{d.day} {d.strftime('%b')}")
    return ', '.join(formatted)


def _send_lateness_report_email(report):
    """Sends the monthly summary and stamps email_sent_at. Synchronous
    by design - see this module's docstring for why a background thread
    is wrong for this specific caller (a one-shot management command),
    even though the same threaded pattern is correct and intentional in
    AttendanceRecord._send_late_email, which runs inside a long-lived
    gunicorn worker instead."""
    from django.core.mail import EmailMultiAlternatives

    employee = report.employee
    to_email = employee.user.email
    month_label = report.month.strftime('%B %Y')
    late_dates_formatted = _format_late_dates(report.late_dates)

    try:
        plain_body = (
            f'Dear {employee.full_name},\n\n'
            f'You have accumulated {report.total_lates} late arrival(s) in {month_label}. '
            f'According to company policy (3 lates = 1 absence), this is calculated as '
            f'{report.converted_absences} absence day(s) under this policy.\n\n'
            f'Total Lates: {report.total_lates}\n'
            f'Late Dates: {late_dates_formatted}\n'
            f'Converted Absences: {report.total_lates} lates \u00f7 3 = {report.converted_absences} day(s)\n'
            f'Monthly Attendance Impact: {report.converted_absences} absence day(s) calculated under this policy - to be applied by HR\n\n'
            'For questions or corrections, please contact HR.\n\n'
            'Kind regards,\n'
            'Admin Team\n\n'
            'Leap Networks Arabia\n'
            'P.O. Box \u2013 70005, Al-Khobar-31952, Kingdom of Saudi Arabia\n'
            'TEL: (+966) 13 8491867 X 108\n'
            'Web: www.leap-arabia.com\n\n'
            'Disclaimer: This email and its attachments are confidential and intended only for '
            'the recipient(s). If you are not the intended recipient, please notify the sender '
            'and delete the message. Unauthorized use, disclosure, or distribution of the email '
            'or the documents is prohibited. Leap Networks Arabia complies with GDPR and ensures '
            'the security of personal data. While we take steps to protect against malware, we '
            "cannot guarantee the email's security. Please verify any information before acting on it."
        )
        html_body = _build_lateness_report_email_html(
            employee_name=employee.full_name, month_label=month_label,
            total_lates=report.total_lates, late_dates_formatted=late_dates_formatted,
            converted_absences=report.converted_absences,
        )
        msg = EmailMultiAlternatives(
            subject=f'Attendance Alert - Lateness Summary for {month_label}',
            body=plain_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        report.email_sent_at = timezone.now()
        report.save(update_fields=['email_sent_at'])
    except Exception:
        logger.exception('Failed to send monthly lateness report email to %s', to_email)


def _build_lateness_report_email_html(employee_name, month_label, total_lates,
                                       late_dates_formatted, converted_absences):
    """Build the branded HTML version of the monthly lateness summary,
    matching accounts.views._build_reset_email_html's styling and the
    same letter format used in AttendanceRecord._send_late_email."""
    return f'''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:#f4f4f4; font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4; padding:30px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 4px 20px rgba(0,0,0,0.08);">

    <!-- Header -->
    <tr>
        <td bgcolor="#C41E3A" style="background:linear-gradient(135deg,#C41E3A,#a01830); padding:35px 40px; text-align:center;">
            <h1 style="color:#ffffff; margin:0; font-size:22px; font-weight:700; letter-spacing:0.5px;">Lateness Summary</h1>
            <p style="color:#ffe5e5; margin:8px 0 0; font-size:13px;">{month_label}</p>
        </td>
    </tr>

    <!-- Body -->
    <tr>
        <td style="padding:40px;">
            <p style="color:#333; font-size:16px; margin:0 0 20px;">Dear {employee_name},</p>

            <p style="color:#555; font-size:14px; line-height:1.7; margin:0 0 25px;">
                You have accumulated <strong>{total_lates}</strong> late arrival(s) in {month_label}.
                According to company policy (3 lates = 1 absence), this is calculated as
                <strong>{converted_absences}</strong> absence day(s) under this policy.
            </p>

            <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fa; border-radius:8px; border-left:4px solid #C41E3A;">
            <tr><td style="padding:18px 22px;">
                <p style="color:#555; font-size:13px; margin:0 0 10px; line-height:1.6;">
                    <strong>Total Lates:</strong> {total_lates}
                </p>
                <p style="color:#555; font-size:13px; margin:0 0 10px; line-height:1.6;">
                    <strong>Late Dates:</strong> {late_dates_formatted}
                </p>
                <p style="color:#555; font-size:13px; margin:0 0 10px; line-height:1.6;">
                    <strong>Converted Absences:</strong> {total_lates} lates &divide; 3 = {converted_absences} day(s)
                </p>
                <p style="color:#555; font-size:13px; margin:0; line-height:1.6;">
                    <strong>Monthly Attendance Impact:</strong> {converted_absences} absence day(s) calculated under this policy &ndash; to be applied by HR
                </p>
            </td></tr>
            </table>

            <p style="color:#888; font-size:12px; line-height:1.6; margin:25px 0 30px;">
                For questions or corrections, please contact HR.
            </p>

            <p style="color:#333; font-size:14px; margin:0 0 4px;">Kind regards,</p>
            <p style="color:#333; font-size:14px; margin:0 0 15px;">Admin Team</p>
            <img src="https://leap-erp.onrender.com/static/images/leap_logo.jpg" alt="Leap Networks Arabia" style="max-width:160px; margin-bottom:20px; display:block;" />

            <p style="color:#777; font-size:12px; line-height:1.6; margin:0;">
                Leap Networks Arabia<br>
                P.O. Box &ndash; 70005, Al-Khobar-31952, Kingdom of Saudi Arabia<br>
                TEL: (+966) 13 8491867 X 108<br>
                Web: <a href="https://www.leap-arabia.com" style="color:#C41E3A; text-decoration:none;">www.leap-arabia.com</a>
            </p>
        </td>
    </tr>

    <!-- Footer / Disclaimer -->
    <tr>
        <td style="background:#2a2a2a; padding:22px 40px;">
            <p style="color:#999; font-size:10px; line-height:1.6; margin:0;">
                Disclaimer: This email and its attachments are confidential and intended only for the recipient(s).
                If you are not the intended recipient, please notify the sender and delete the message.
                Unauthorized use, disclosure, or distribution of the email or the documents is prohibited.
                Leap Networks Arabia complies with GDPR and ensures the security of personal data.
                While we take steps to protect against malware, we cannot guarantee the email's security.
                Please verify any information before acting on it.
            </p>
        </td>
    </tr>

</table>
</td></tr>
</table>
</body>
</html>'''


# ── Generating from the ERP ──────────────────────────────────────────────
# The command above is the only thing that ever called the generator, and the
# production web service has no shell and no cron job - so in production no
# report has ever been generated. These are what the HR screen calls instead.
#
# They only ever target a month that has already FINISHED. That is not a
# restriction bolted on for safety, it is the better way to run this at all:
# the generator freezes each employee's snapshot the first time it runs for a
# month (get_or_create), so the cron design had to guess a moment late on the
# last day and lose anything after it. Run once the month is over, the whole
# month is in, and the partial-month lock the module docstring warns about
# cannot happen.

def _month_start(d):
    return d.replace(day=1)


def is_completed_month(target_month, today=None):
    """True if target_month is strictly before the current (Riyadh) month."""
    today = today or timezone.localtime(timezone.now()).date()
    return _month_start(target_month) < _month_start(today)


def generate_for_completed_month(target_month, today=None):
    """Generate and email the reports for a month that has already ended.

    Raises ValueError for the current or a future month rather than quietly
    doing nothing: a report generated mid-month is locked in forever by the
    idempotency above, so refusing loudly is the whole point.

    Safe to repeat. Existing reports are never regenerated, and an email that
    failed last time is retried.
    """
    today = today or timezone.localtime(timezone.now()).date()
    if not is_completed_month(target_month, today):
        raise ValueError(
            f'{target_month:%B %Y} has not finished yet. A report generated now '
            f'would be frozen with only part of the month in it, so it can only '
            f'be generated once the month is over.')
    return generate_monthly_lateness_reports(
        today=today, _skip_last_day_check=True,
        target_month=_month_start(target_month))


def completed_months_needing_reports(today=None, lookback=6):
    """Finished months that have late arrivals but no reports at all.

    A month with nobody late produces no report rows, so "no rows" cannot on
    its own mean "never generated". Having undisputed lates and no rows can -
    that is the case worth putting in front of HR, because it means employees
    are owed a notice they never received.
    """
    from hr.models import AttendanceRecord, MonthlyLatenessReport, LateQuery

    today = today or timezone.localtime(timezone.now()).date()
    current = _month_start(today)
    disputed = set(LateQuery.objects.filter(status='pending')
                   .values_list('attendance_record_id', flat=True))
    months = []
    cursor = current
    for _ in range(lookback):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        last = date_cls(cursor.year, cursor.month,
                        calendar.monthrange(cursor.year, cursor.month)[1])
        has_lates = (AttendanceRecord.objects
                     .filter(status='late', date__gte=cursor, date__lte=last,
                             employee__is_active=True)
                     .exclude(pk__in=disputed).exists())
        if has_lates and not MonthlyLatenessReport.objects.filter(month=cursor).exists():
            months.append(cursor)
    return months


def recent_completed_months(today=None, count=12):
    """The finished months HR may pick from, newest first."""
    today = today or timezone.localtime(timezone.now()).date()
    cursor = _month_start(today)
    months = []
    for _ in range(count):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        months.append(cursor)
    return months
