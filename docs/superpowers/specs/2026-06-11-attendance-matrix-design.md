# Attendance Matrix (Weekly/Monthly Register) — Design Spec

**Date:** 2026-06-11
**Status:** Approved for planning
**Author:** Aqsa Ahmed (with Claude)

## Problem / Goal

The HR attendance module currently has a **daily grid** (all employees × one day) and a
**per-employee monthly history list**. HR wants an at-a-glance **weekly and monthly
register** across all employees, and the ability to **mark any employee on leave** directly
from that view, with the leave reflected in the grid (the grid doubles as the "calendar").

## Decisions locked during brainstorming

- **Layout:** an all-employees **matrix** (rows = active employees, columns = days). Weekly =
  7 day-columns, monthly = the calendar month's day-columns. The matrix **is** the calendar
  (leave shows as an `L` cell) — no separate calendar widget/library.
- **Mark leave:** click a working cell → inline picker (active leave types, default Annual) →
  creates a **1-day LeaveRecord** (AJAX); the cell flips to `L`. Multi-day leave uses the
  existing range form (`LeaveRecordCreateView`).
- **Read-only for attendance:** check-in/out entry stays in the daily grid; the matrix is an
  overview + leave-marking surface.
- **No calendar library:** server-rendered Bootstrap table + small vanilla-JS for the
  click-to-mark AJAX, consistent with the rest of the app.
- **Permissions:** reuse the existing `AdminRequiredMixin` / admin-gate pattern.

## Architecture

### 1. Matrix view — `attendance_matrix` (function view, admin-gated)

**URL:** `path('attendance/matrix/', views.attendance_matrix, name='attendance_matrix')`.

**Query params:**
- `period` = `week` | `month` (default `month`).
- `date` = `YYYY-MM-DD` anchor (default today, via the existing `_parse_date`).
- Navigation renders ‹ prev / Today / next › links that shift the anchor by one period.

**Date range:**
- `week`: the 7 days of the week containing the anchor, **starting Sunday** (Sun–Sat).
- `month`: day 1 → last day of the anchor's calendar month.

**Batched cell computation (no per-cell queries):**
1. `employees = Employee.objects.filter(is_active=True).order_by('full_name')`.
2. `records = {(employee_id, date): status}` from
   `AttendanceRecord.objects.filter(date__range=(start, end))`.
3. `leave_days = {(employee_id, date)}` — expand `LeaveRecord.objects.filter(start_date__lte=end, end_date__gte=start)` to the set of (employee, day) covered within the range.
4. `holidays = {date}` from active `Holiday` rows in the range; `weekends = AttendanceSettings.load().weekend_day_set()`.
5. For each (employee, day) cell, resolve status by precedence:
   - a stored `AttendanceRecord.status` for that (emp, day) **wins** (it already encodes
     leave/holiday/weekend/present/absent as derived at save time);
   - else `leave` if in `leave_days`; else `holiday` if the day is a holiday; else `weekend`
     if the weekday is a weekend; else `''` (blank — **not yet recorded**, shown as `—`).

The view builds a `rows` structure: `[{employee, cells: [{date, status, leave_record_id, locked}]}]`
where `locked` marks weekend/holiday cells (not clickable to mark leave). `leave_record_id` is
the pk of the covering LeaveRecord when it is a **single-day** record (start==end==date),
enabling the remove toggle; `None` otherwise.

The template renders a horizontally-scrollable Bootstrap table; columns shade weekend/holiday;
cells are color-coded badges/letters: present=success, absent=danger, leave=info, holiday=primary,
weekend=secondary, blank=`—`.

### 2. Mark leave from a cell — `attendance_mark_leave` (AJAX, admin-gated, POST)

**URL:** `path('attendance/mark-leave/', views.attendance_mark_leave, name='attendance_mark_leave')`.

**Request (JSON or form POST):** `employee` (pk), `date` (YYYY-MM-DD), `leave_type` (pk).

**Behavior (in `transaction.atomic()`):**
- Admin gate (super_admin/admin) + `@require_POST`.
- Validate employee active, leave_type active, date parses.
- Create a 1-day `LeaveRecord(employee, leave_type, start_date=date, end_date=date, created_by=user)`
  (days auto-computes to 1 working day, or 0 if the day is weekend/holiday — but cells on
  weekend/holiday are not clickable, so date is always a working day here).
- **Consistency:** `update_or_create` the `AttendanceRecord` for (employee, date) with
  `status='leave', check_in=None, check_out=None, hours_worked=None`, so the daily grid agrees.
- Return JSON `{ok: True, status: 'leave', leave_record_id: <pk>}`.

**Client JS:** on cell click (working cell), show the inline leave-type picker; on Confirm,
POST; on success, set the cell to the `L` badge and store `leave_record_id` for the remove toggle.

### 3. Remove leave (toggle) — `attendance_unmark_leave` (AJAX, admin-gated, POST)

**URL:** `path('attendance/unmark-leave/', views.attendance_unmark_leave, name='attendance_unmark_leave')`.

**Request:** `leave_record_id`.

**Behavior (atomic):**
- Admin gate + `@require_POST`. Load the LeaveRecord.
- Guard: only **single-day** records (`start_date == end_date`) are removable here; a multi-day
  record returns `{ok: False, error: 'Part of a multi-day leave — edit from the leave summary.'}`
  with HTTP 400, and the JS shows that message.
- Delete the record; **re-derive** that day's `AttendanceRecord` via `derive_status` and update
  it (status back to weekend/holiday/absent/present as appropriate). Return `{ok: True, status: <new>}`.

**Client JS:** clicking a single-day `L` cell offers "Remove leave"; on success the cell reverts
to its re-derived status.

### 4. Range leave — reuse existing

A **"Mark leave (range)"** button on the matrix links to the existing
`hr:leave_record_create` (the `LeaveRecordCreateView` from Phase A: employee, start, end, type).
No new form/model.

### 5. Navigation

Add a **"Register"** link under the existing **Attendance** sidebar submenu in `base.html`
(alongside Daily Grid + Settings), pointing to `hr:attendance_matrix`.

## Components & boundaries

| Unit | Responsibility |
|---|---|
| `attendance_matrix` view + `hr/attendance_matrix.py` helper | Compute the batched cell grid for a period |
| `attendance_mark_leave` / `attendance_unmark_leave` views | AJAX create/remove a 1-day leave + keep AttendanceRecord consistent |
| `templates/hr/attendance_matrix.html` | Render the register + inline picker + JS |
| `base.html` | Register nav link |

A small `hr/attendance_matrix.py` (or a function in `attendance_services.py`) holds
`build_matrix(employees, start, end)` so the batched logic is unit-testable without HTTP.

## Edge cases

- **Cell already on leave (multi-day):** not removable from the cell (guarded); editable via the
  leave summary. The cell still shows `L`.
- **Marking leave on a day with a present AttendanceRecord:** the mark endpoint overwrites that
  day's AttendanceRecord to `leave` (and the LeaveRecord drives the balance) — consistent with
  the leave>present precedence.
- **Blank/unrecorded working days** render `—` (distinct from explicit `absent`).
- **Large months / many employees:** batched queries keep it to ~4 queries regardless of grid
  size; the table scrolls horizontally for monthly view.
- **Period math at month/year boundaries:** prev/next on the last week of a month or December
  must roll over correctly (use `datetime`/`calendar` helpers, not manual arithmetic).
- **Inactive employees:** excluded from rows (matches the daily grid).

## Testing

- `build_matrix` cell precedence: stored record wins; leave/holiday/weekend/blank resolution;
  single-day vs multi-day `leave_record_id`.
- Week range starts Sunday; month range spans the full calendar month; prev/next navigation math.
- `attendance_mark_leave`: creates a 1-day LeaveRecord, upserts the AttendanceRecord to `leave`,
  decrements balance, admin-gated, POST-only.
- `attendance_unmark_leave`: deletes a single-day record + re-derives the day; rejects multi-day
  (400); admin-gated, POST-only.
- Matrix view renders rows for active employees and the period header; nav link present.

## Out of scope (this spec)

- Editing check-in/out times from the matrix (stays in the daily grid).
- Click-drag multi-cell selection (range uses the existing form).
- A JS calendar library / separate calendar page.
- Per-region weekends, half-day leave, approval workflow (already out of scope project-wide).
- Exporting the matrix to Excel (could be a later add).
