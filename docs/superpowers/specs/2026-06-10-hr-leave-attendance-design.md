# HR Leave & Daily Attendance — Design Spec

**Date:** 2026-06-10
**Status:** Approved for planning
**Author:** Aqsa Ahmed (with Claude)

## Problem / Goal

The `hr` app has rich `Employee` master records but no way to track **leave/vacation
entitlements and balances**, nor **daily attendance (check-in/check-out)**. Add both,
HR-administered, fitting the existing HR module and Saudi labor context (Leap Networks
Arabia). The two are coupled: attendance must know when an employee is on leave or it's
a holiday/weekend so those days aren't flagged "Absent."

## Decisions locked during brainstorming

- **Actors:** HR-administered. Employees stay pure `hr.Employee` records with **no login
  accounts**. HR/admin staff record attendance and leave on employees' behalf. No
  `Employee↔User` link is introduced.
- **Leave depth:** Entitlement + balance. Each employee has an annual quota per leave type;
  HR records each leave period; balance (`entitled | taken | remaining`) is computed.
- **Attendance depth:** Store check-in/out per employee per day, auto-compute hours worked,
  and be leave/holiday/weekend aware (auto-classify those days instead of "Absent").
- **Entry method:** A bulk daily grid (one date, all active employees) plus a per-employee
  history view.
- **Non-working days:** A `Holiday` calendar plus a configurable weekend (default Fri–Sat).
- **Leave day counting:** Excludes weekends and holidays (a Sun–Thu leave that spans a
  Fri–Sat counts the working days only).
- **Models layout:** Split the HR app's models into an `hr/models/` package.
- **Permissions:** Reuse the existing `AdminRequiredMixin` (super_admin/admin) used by the
  current HR views.

## Architecture

### Models package split

Convert `hr/models.py` into a package `hr/models/`:

| File | Models | Status |
|---|---|---|
| `hr/models/__init__.py` | re-exports all (so `from hr.models import Employee` etc. keep working) | new |
| `hr/models/employee.py` | `Employee`, `EmployeeDocument` | moved (unchanged) |
| `hr/models/assets.py` | `Asset`, `AssetAssignment`, `Vehicle` | moved (unchanged) |
| `hr/models/leave.py` | `LeaveType`, `LeaveEntitlement`, `LeaveRecord` | new |
| `hr/models/attendance.py` | `Holiday`, `AttendanceSettings`, `AttendanceRecord` | new |

Moving models between files **within the same app does not create migrations** (Django
tracks by `app_label` + model name). To be safe each model in a submodule sets
`Meta.app_label = 'hr'` (or relies on the `__init__` imports running at app load); the
implementation must run `makemigrations` and confirm only *new-model* migrations appear,
no spurious deletes/recreates of `Employee`/`Asset`/`Vehicle`.

### Shared calendar helper — `hr/work_calendar.py`

A small module both subsystems use:

```python
def weekend_days() -> set[int]      # from AttendanceSettings singleton; Mon=0..Sun=6; default {4,5} (Fri,Sat)
def holiday_dates(year) -> set[date]  # active Holiday rows
def is_working_day(d, weekends, holidays) -> bool
def count_working_days(start, end, weekends, holidays) -> int   # inclusive range, excludes weekend+holiday
```

### Module A — Leave / Vacation

**`LeaveType`**
- `name`, `code` (unique), `default_annual_days` (Decimal), `is_paid` (bool),
  `color` (CharField, for UI badges), `is_active` (bool), audit (`created_at`).
- Seed data: Annual (`default_annual_days=21`, paid), Sick (paid), Unpaid (0, unpaid).

**`LeaveEntitlement`** — the per-employee "structure"
- `employee` FK→Employee (CASCADE), `leave_type` FK→LeaveType (PROTECT), `year` (int),
  `entitled_days` (Decimal), audit (`created_by`, `created_at`, `updated_at`).
- `unique_together = (employee, leave_type, year)`.
- Properties: `taken_days` (Σ `LeaveRecord.days` for same employee+type+year), `remaining_days`.

**`LeaveRecord`** — a recorded leave period
- `employee` FK→Employee (CASCADE), `leave_type` FK→LeaveType (PROTECT),
  `start_date`, `end_date`, `days` (Decimal), `note`, audit (`created_by`, `created_at`).
- `days` defaults to `count_working_days(start, end, ...)` on save, but is **HR-editable**
  (override stored). `clean()` enforces `end_date >= start_date`.
- Balance attribution: counts toward the year of `start_date` (cross-year leave is an edge
  case — see Edge cases).

**Year-entitlement generator** (a view action): for a chosen `year`, create/refresh
`LeaveEntitlement` rows for all active employees from each active `LeaveType.default_annual_days`,
EXCEPT **Annual**, which follows the **Leap Networks policy** per employee:
**25 days in the employee's first year of service, 30 days from the second year onward.**
The 12-month anniversary upgrades the employee to 30, and the year containing that anniversary
gets 30 ("anniversary year → 30"). Because the anniversary always falls in
`joining_date.year + 1`, this reduces to a clean per-calendar-year rule:

```
entitled = 25 if year <= joining_date.year else 30
```

i.e. the calendar year they joined → 25; every subsequent calendar year → 30. (Example: joins
1 Jul 2025 → 2025 = 25, 2026 = 30, 2027+ = 30.) Existing rows are not overwritten unless HR
explicitly chooses "reset" (default: only create missing).

### Module B — Daily Attendance

**`Holiday`** — `date` (unique), `name`, `is_active`, audit. (Region tagging deferred.)

**`AttendanceSettings`** — singleton (enforced `pk=1`)
- `weekend_days` — stored as a comma string of weekday ints (default `"4,5"` = Fri,Sat),
  edited via a small HR settings form. Helper parses to `set[int]`.

**`AttendanceRecord`**
- `employee` FK→Employee (CASCADE), `date`, `check_in` (TimeField null), `check_out`
  (TimeField null), `status` (CharField choices), `hours_worked` (Decimal null), `note`,
  audit (`created_by`, `created_at`, `updated_at`).
- `unique_together = (employee, date)`.
- **Status derivation** (computed and STORED on save, recomputable via a "regenerate" action):
  precedence — (1) a `LeaveRecord` covers `(employee, date)` → **`leave`**;
  (2) else `date` is an active `Holiday` → **`holiday`**;
  (3) else `date` weekday ∈ weekend → **`weekend`**;
  (4) else `check_in` is set → **`present`** (and `hours_worked = check_out − check_in`
  when both set; if only check_in, hours stays null/0);
  (5) else → **`absent`**.
- Storing status (vs pure live-compute) is deliberate so absence/attendance reports are a
  simple indexed query. The "regenerate day/month" action re-derives status after leave or
  holidays change.

### Views / URLs / Templates (under the `hr` app)

Follow existing `hr` conventions (CBV + `AdminRequiredMixin`, Bootstrap, `app/Model_*.html`,
`paginate_by = 25`).

**Leave:**
- `LeaveType` list/create/update (admin-config).
- `Holiday` list/create/update/delete.
- Leave entitlements: a per-year management page + the **"Generate year entitlements"** action.
- `LeaveRecord` create/update/delete (from an employee's Leave tab and a global list).
- Employee **Leave summary**: the `entitled | taken | remaining` table per type for a year,
  plus that employee's leave records.

**Attendance:**
- **Daily grid** (function view): `GET ?date=YYYY-MM-DD` renders all active employees with
  check-in/out inputs; rows where the day is leave/holiday/weekend are pre-filled and
  read-only with the classification shown; `POST` upserts all rows (one `AttendanceRecord`
  per employee) and derives status. Defaults to today.
- **Per-employee attendance history** + **monthly summary** (present/absent/leave/holiday
  counts and total hours for a month).
- A small **AttendanceSettings** edit form (weekend days).

URLs namespaced in `hr/urls.py` (e.g. `attendance_grid`, `attendance_history`, `leave_record_create`,
`leave_entitlement_year`, `holiday_list`, ...). Sidebar links added to the HR section in `base.html`.

### Permissions

All new views use the existing `AdminRequiredMixin` (`is_super_admin_user or is_admin_user`),
matching the current HR module. (A future `hr.*` capability in the new permission system could
gate this more granularly — out of scope here.)

## Build order (one spec, two implementation phases)

- **Phase A — Leave + calendar foundation:** models package split; `LeaveType`,
  `LeaveEntitlement`, `LeaveRecord`, balances, Saudi year generator; `Holiday`,
  `AttendanceSettings`; `work_calendar.py` helpers; leave/holiday views + templates. Seed leave types.
- **Phase B — Attendance:** `AttendanceRecord`, status derivation, the leave/holiday/weekend-aware
  daily grid, per-employee history, monthly summary, settings form.

Phase B depends on Phase A (it reads leave + holidays + weekend settings).

## Edge cases

- **Cross-year leave** (e.g. Dec 30 → Jan 3): counted toward the `start_date` year for balance;
  attendance still classifies each day correctly by its own date. Acceptable; documented.
- **Leave fully inside a weekend/holiday:** `count_working_days` returns 0 → `days = 0`
  (HR can override if policy differs).
- **Check-out before check-in / missing check-out:** `clean()` rejects `check_out < check_in`;
  a check-in with no check-out → `present`, `hours_worked` null (HR completes later).
- **Employee deactivated mid-period:** grid lists `is_active=True` only; historical records remain.
- **AttendanceSettings/Holiday changed after records exist:** stored statuses go stale until the
  "regenerate" action is run — surfaced as an explicit HR action, not silent.
- **Singleton AttendanceSettings:** always `get_or_create(pk=1)`.

## Testing

- `count_working_days` across weekends + holidays (incl. zero-working-day ranges).
- Leap Networks annual rule: joining calendar year → `25`, every year after → `30`
  (from `joining_date`); verify the boundary at the year an employee crosses from first to
  second year.
- Leave balance: `entitled | taken | remaining` with multiple records; cross-year attribution.
- Attendance status precedence: leave > holiday > weekend > present > absent, for the same date.
- `hours_worked` computation; check_out<check_in rejected.
- Daily grid POST upserts (no duplicate `(employee, date)`), pre-locks leave/holiday rows.
- Models package split: `makemigrations` produces only new-model migrations, no churn on
  Employee/Asset/Vehicle; `from hr.models import Employee` still imports.

## Out of scope (this spec)

- Employee self-service / employee logins.
- Half-day leave, hourly leave, break-time deduction in hours, overtime rules.
- Leave approval workflow / accrual ledger / carry-over (we record taken leave directly).
- Per-region weekend variation and per-region holiday tagging (single global weekend + flat
  holiday list for now).
- Biometric/clock-device integration; payroll/`vacation_pay` linkage to the `manpower` app.
- Gating via the new capability system (uses existing `AdminRequiredMixin`).
