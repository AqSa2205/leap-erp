"""Seed local demo data for manually verifying the post-vacation leave
cooldown feature (see hr.leave_cooldown.annual_leave_cooldown), reusing
EXISTING employees rather than creating new fake ones, so it can be tried
straight from their own My Profile pages.

Two of the three cooldown tiers already show up naturally in the existing
demo dataset, so this command leaves them untouched and just reports on
them:
  - omar.khalid   -> already blocked (Rule 1: last leave was under 10 days,
                     1-month cooldown) purely from pre-existing seed data.
  - tariq.rahman  -> already free to apply (their only Annual leave record
                     is in the future, so there's nothing to cool down from
                     yet) - a good "not blocked" control case.

This command adds two more LeaveRecord rows (tagged with a
'DEMO-COOLDOWN:' note, so they're easy to spot and safe to remove) to
demonstrate the two tiers that aren't naturally present yet:
  - mona.farooq    -> Rule 2: a 15-day leave, 3-month cooldown.
  - bilal.siddiqui -> Rule 3: used their FULL entitlement in one booking.
                       They already have a joining_date set (2022-03-15),
                       so this also proves the eligible date lands on their
                       joining anniversary, not a fixed calendar date.
  - reem.qureshi   -> Rule 3 again, but with no joining_date set, to show
                       the "1 Jan next year" fallback path instead.
  - zeeshan.iqbal  -> Rule 3, CUMULATIVE path: three separate 10-day
                       bookings across the year that together add up to
                       the full entitlement — no single booking used it
                       all, but the running total does. Proves Rule 3 is
                       not limited to a single big booking.

All six accounts get their password set to DemoPass123! so you can log in
as each one and check My Profile directly. Safe to re-run.

Run:       python manage.py seed_leave_cooldown_demo
Undo:      python manage.py seed_leave_cooldown_demo --wipe
"""
from datetime import timedelta, date
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model

from hr.models import Employee, LeaveType, LeaveEntitlement, LeaveRecord
from hr.leave_cooldown import annual_leave_cooldown

User = get_user_model()
DEMO_NOTE = 'DEMO-COOLDOWN: seeded by seed_leave_cooldown_demo'
DEMO_PASSWORD = 'DemoPass123!'


class Command(BaseCommand):
    help = 'Seed (or --wipe) demo data for the post-vacation leave cooldown feature, using existing employees.'

    def add_arguments(self, parser):
        parser.add_argument('--wipe', action='store_true', help='Remove the demo leave records instead of creating them.')

    def handle(self, *args, **options):
        try:
            lt = LeaveType.objects.get(code='annual')
        except LeaveType.DoesNotExist:
            raise CommandError("No LeaveType with code='annual' found — seed leave types first.")

        removed = LeaveRecord.objects.filter(leave_type=lt, note=DEMO_NOTE)
        if removed.exists():
            count = removed.count()
            removed.delete()
            self.stdout.write(f'Removed {count} existing demo leave record(s).')

        if options['wipe']:
            self.stdout.write(self.style.SUCCESS('Demo cooldown data wiped.'))
            return

        today = date.today()
        year = today.year

        def get_employee(username):
            try:
                return Employee.objects.get(user__username=username)
            except Employee.DoesNotExist:
                raise CommandError(f'No employee found with username "{username}" — is the base demo data seeded?')

        def ensure_entitlement(emp):
            ent, _ = LeaveEntitlement.objects.get_or_create(
                employee=emp, leave_type=lt, year=year,
                defaults={'entitled_days': lt.default_days_for(emp.work_location)})
            return ent

        def set_login(emp):
            if emp.user_id:
                emp.user.set_password(DEMO_PASSWORD)
                emp.user.save(update_fields=['password'])

        # --- Rule 2: 15-day leave -> 3-month cooldown ---------------------
        # Ends yesterday (not weeks ago) so the "days remaining" badge shows
        # the FULL ~3-month window, not a partially-elapsed slice of it.
        mona = get_employee('mona.farooq')
        ensure_entitlement(mona)
        mona_end = today - timedelta(days=1)
        LeaveRecord.objects.create(
            employee=mona, leave_type=lt,
            start_date=mona_end - timedelta(days=14), end_date=mona_end,
            note=DEMO_NOTE)
        set_login(mona)

        # --- Rule 3, joining-anniversary path ------------------------------
        bilal = get_employee('bilal.siddiqui')
        bilal_ent = ensure_entitlement(bilal)
        bilal_end = today - timedelta(days=60)
        LeaveRecord.objects.create(
            employee=bilal, leave_type=lt,
            start_date=bilal_end - timedelta(days=int(bilal_ent.entitled_days) - 1), end_date=bilal_end,
            days=bilal_ent.entitled_days, note=DEMO_NOTE)
        set_login(bilal)

        # --- Rule 3, no-joining_date fallback path -------------------------
        reem = get_employee('reem.qureshi')
        reem_ent = ensure_entitlement(reem)
        reem_end = today - timedelta(days=90)
        LeaveRecord.objects.create(
            employee=reem, leave_type=lt,
            start_date=reem_end - timedelta(days=int(reem_ent.entitled_days) - 1), end_date=reem_end,
            days=reem_ent.entitled_days, note=DEMO_NOTE)
        set_login(reem)

        # --- Rule 3, CUMULATIVE across 3 separate 10-day bookings ----------
        zeeshan = get_employee('zeeshan.iqbal')
        ensure_entitlement(zeeshan)
        z3_end = today - timedelta(days=1)     # most recent booking ends yesterday
        z3_start = z3_end - timedelta(days=9)
        z2_end = z3_start - timedelta(days=21)
        z2_start = z2_end - timedelta(days=9)
        z1_end = z2_start - timedelta(days=21)
        z1_start = z1_end - timedelta(days=9)
        # .create() (not bulk_create) so LeaveRecord.save() auto-computes
        # `days` from the date range, same as every other record here.
        LeaveRecord.objects.create(employee=zeeshan, leave_type=lt, start_date=z1_start, end_date=z1_end, note=DEMO_NOTE)
        LeaveRecord.objects.create(employee=zeeshan, leave_type=lt, start_date=z2_start, end_date=z2_end, note=DEMO_NOTE)
        LeaveRecord.objects.create(employee=zeeshan, leave_type=lt, start_date=z3_start, end_date=z3_end, note=DEMO_NOTE)
        set_login(zeeshan)

        # --- Untouched: already demonstrate Rule 1 / "free to apply" -------
        omar = get_employee('omar.khalid')
        set_login(omar)
        tariq = get_employee('tariq.rahman')
        set_login(tariq)

        def describe(emp):
            cd = annual_leave_cooldown(emp, lt, today=today)
            if cd is None:
                return 'FREE to apply right now.'
            return f"BLOCKED until {cd['eligible_date']:%d %b %Y} — \"{cd['reason']}\""

        self.stdout.write(self.style.SUCCESS(
            f"Seeded/verified leave-cooldown demo scenarios as of {today:%d %b %Y}. "
            f"All logins below use password: {DEMO_PASSWORD}\n\n"
            f"  1) omar.khalid    (Rule 1 - short leave, pre-existing data)\n"
            f"     {describe(omar)}\n\n"
            f"  2) tariq.rahman   (control case - nothing blocking them)\n"
            f"     {describe(tariq)}\n\n"
            f"  3) mona.farooq    (Rule 2 - 15-day leave, 3-month cooldown)\n"
            f"     {describe(mona)}\n\n"
            f"  4) bilal.siddiqui (Rule 3 - full entitlement, joining-anniversary reset)\n"
            f"     {describe(bilal)}\n\n"
            f"  5) reem.qureshi   (Rule 3 - full entitlement, no joining_date -> 1 Jan fallback)\n"
            f"     {describe(reem)}\n\n"
            f"  6) zeeshan.iqbal  (Rule 3 - CUMULATIVE: three separate 10-day bookings add up to 30/30)\n"
            f"     {describe(zeeshan)}\n\n"
            f"Log in as each username above at /accounts/login/ and open My Profile to see the "
            f"warning banner and the blocked submission message live.\n"
            f"Run with --wipe to remove the seeded leave records again (omar.khalid and "
            f"tariq.rahman are never modified beyond their password)."
        ))
