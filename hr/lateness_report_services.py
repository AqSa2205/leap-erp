"""Month-end lateness-to-absence consequence report. Every 3 late arrivals
in a month convert to 1 day of absence under company policy - this module
generates a per-employee summary of that pattern and emails it to anyone
with at least one late arrival that month. Purely a record and
notification: never touches AttendanceRecord rows or any existing
absence/leave totals, and late_dates is snapshotted at generation time
rather than derived live, so the report reflects what was true when it
ran even if underlying records are corrected afterward.
"""
import calendar
import logging
import sys
from datetime import date as date_cls

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Sending synchronously during tests avoids a real SQLite-locking race:
# TransactionTestCase does real commits and flushes tables between tests,
# but a fire-and-forget background thread from one test's email send can
# still be writing (report.save(update_fields=[...])) when the next test
# starts, and SQLite only allows one writer at a time.
_RUNNING_TESTS = 'test' in sys.argv


def is_last_day_of_month(d):
    """True if d is the final calendar day of its month - the trigger
    condition generate_monthly_lateness_reports checks, so the
    underlying management command can safely run daily via cron and
    only actually do anything once a month."""
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.day == last_day


def generate_monthly_lateness_reports(today=None, _skip_last_day_check=False):
    """For every ACTIVE employee with at least one 'late' AttendanceRecord
    in the current month (from the 1st up to and including `today`),
    create a MonthlyLatenessReport snapshot and email them the summary.
    Report creation is idempotent per (employee, month) via
    get_or_create - re-running never duplicates or overwrites an
    existing report's snapshot. The email send retries on every call
    until it succeeds (report.email_sent_at is None) - a transient
    failure (network blip, mail service down) on one run doesn't
    silently mean the employee never hears about it; once sent, it's
    never resent. Returns the number of NEW reports created this call
    (retried sends on already-existing reports don't add to this
    count).

    Refuses to run (returns 0, logs a warning) unless `today` is
    genuinely the last day of its month - a partial-month report would
    otherwise be permanently locked in by the idempotency above, since
    a later correct run on the true last day won't regenerate it. This
    mirrors the management command's own gate, but belongs here too so
    any direct caller (a script, a shell session, a future admin
    action) gets the same protection. Pass _skip_last_day_check=True
    only for a deliberate manual recovery (e.g. the scheduled run was
    missed) - never for routine use."""
    from hr.models import AttendanceRecord, MonthlyLatenessReport

    today = today or timezone.localtime(timezone.now()).date()
    if not _skip_last_day_check and not is_last_day_of_month(today):
        logger.warning(
            'generate_monthly_lateness_reports called with %s, which is not '
            'the last day of its month - refusing to run (a partial-month '
            'report would be permanently locked in). Pass '
            '_skip_last_day_check=True for a deliberate manual recovery.', today)
        return 0
    month_start = today.replace(day=1)

    late_records = (
        AttendanceRecord.objects.filter(
            status='late', date__gte=month_start, date__lte=today,
            employee__is_active=True)
        .select_related('employee').order_by('employee_id', 'date')
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
    """Sends the monthly summary and stamps email_sent_at - mirrors the
    threaded-send pattern used by AttendanceRecord._send_late_email
    (hr/models/attendance.py), including the same branded HTML
    alternative and letter format (logo after 'Admin Team', address
    block, confidentiality disclaimer)."""
    import threading
    from django.core.mail import EmailMultiAlternatives
    from django.db import connections

    employee = report.employee
    to_email = employee.user.email
    month_label = report.month.strftime('%B %Y')
    late_dates_formatted = _format_late_dates(report.late_dates)

    def _send():
        try:
            plain_body = (
                f'Dear {employee.full_name},\n\n'
                f'You have accumulated {report.total_lates} late arrival(s) in {month_label}. '
                f'According to company policy (3 lates = 1 absence), this results in '
                f'{report.converted_absences} absence day(s) added to your monthly attendance record.\n\n'
                f'Total Lates: {report.total_lates}\n'
                f'Late Dates: {late_dates_formatted}\n'
                f'Converted Absences: {report.total_lates} lates \u00f7 3 = {report.converted_absences} day(s) absent\n'
                f'Monthly Attendance Impact: +{report.converted_absences} absence day(s) added to your record\n\n'
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
        finally:
            connections.close_all()

    if _RUNNING_TESTS:
        _send()
    else:
        threading.Thread(target=_send, daemon=True).start()


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
                According to company policy (3 lates = 1 absence), this results in
                <strong>{converted_absences}</strong> absence day(s) added to your monthly attendance record.
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
                    <strong>Converted Absences:</strong> {total_lates} lates &divide; 3 = {converted_absences} day(s) absent
                </p>
                <p style="color:#555; font-size:13px; margin:0; line-height:1.6;">
                    <strong>Monthly Attendance Impact:</strong> +{converted_absences} absence day(s) added to your record
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
