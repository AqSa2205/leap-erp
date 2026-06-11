# Attendance: Late, Working-Day Exceptions & WFH — Design Spec

**Date:** 2026-06-11
**Status:** Approved for planning
**Author:** Aqsa Ahmed (with Claude)

## Problem / Goal

Extend the HR attendance system with three requested capabilities: flag **Late** arrivals,
mark a normally-weekend day as a **Working Day** (e.g. an occasional working Saturday), and
record **Work-From-Home (WFH)** — both per day on the grid and as a multi-day record.

## Decisions locked during brainstorming

- **Late:** a separate `late` status, auto-derived from a configurable threshold
  (`expected_in_by`, default 08:30). A check-in after the threshold on a working day → Late
  (still counts hours). HR does not mark it manually.
- **Working Saturday:** a per-date **WorkingDay** exception calendar (the inverse of Holidays).
  A normally-weekend date listed there is treated as a working day.
- **WFH:** **both** — a multi-day `WFHRecord` (like a leave record, but no balance) *and* a
  per-day **WFH** button on the daily grid.

## New statuses & colors

Two new statuses join present/absent/leave/holiday/weekend:

| Status | Grid/history label | Matrix code | Color |
|---|---|---|---|
| Late | Late | **LT** | orange `#fd7e14` |
| WFH | WFH | **RM** | purple `#6f42c1` |

(existing: Present P green, Absent A red, Leave L cyan, Holiday H amber, Weekend W grey)

Two small custom badge classes are added (e.g. in `base.html`):
`.badge-late { background:#fd7e14; color:#fff; }` and `.badge-wfh { background:#6f42c1; color:#fff; }`.

## Architecture

### Models (in the `hr` app)

- **`AttendanceSettings`** (existing singleton) gains **`expected_in_by`** — `TimeField`,
  default `08:30`. Edited on the Attendance Settings page.
- **`WorkingDay`** (new) — `date` (unique), `name` (CharField), `is_active` (bool), `created_at`.
  Mirrors the `Holiday` model; it's the inverse — a date that overrides the weekend rule.
- **`WFHRecord`** (new) — `employee` FK→Employee (CASCADE, related_name `wfh_records`),
  `start_date`, `end_date`, `note`, `created_by`, `created_at`. Ordering `['-start_date']`,
  index `(employee, start_date)`. **No `days`/balance** — WFH is worked time, not leave.
  `clean()` rejects `end_date < start_date`.

`AttendanceRecord.STATUS_CHOICES` gains `('late', 'Late')` and `('wfh', 'WFH')`.

### Status derivation — `hr/attendance_services.derive_status`

New precedence (was leave > holiday > weekend > present > absent):

```
1. a LeaveRecord covers (employee, day)                      -> ('leave', None)
2. an active Holiday on day                                  -> ('holiday', None)
3. day.weekday() in weekend_set AND day NOT an active WorkingDay -> ('weekend', None)
4. a WFHRecord covers (employee, day)                        -> ('wfh', hours(check_in, check_out))
5. check_in is set:
     check_in > AttendanceSettings.expected_in_by           -> ('late', hours(...))
     else                                                    -> ('present', hours(...))
6. else                                                      -> ('absent', None)
```

Notes:
- The WorkingDay exception only matters on a weekend day (step 3); on a normal weekday it's a
  no-op. So a working Saturday falls through to steps 4–6 (WFH/late/present/absent apply).
- WFH beats present/late (a WFH record means the person worked from home that day); hours are
  still computed from any check-in/out entered.
- `hours(check_in, check_out)` reuses the existing `_hours_between` (returns None for inverted
  or missing times; never negative).

### Matrix — `hr/attendance_matrix.build_matrix`

Cell precedence (stored record wins, else derive):

```
stored AttendanceRecord.status (now may be 'late'/'wfh' too)  -> that status
else a LeaveRecord covers it                                  -> 'leave'
else an active Holiday                                        -> 'holiday'
else weekend (weekday in weekend_set AND not a WorkingDay)    -> 'weekend'
else a WFHRecord covers it                                    -> 'wfh'   (single-day -> removable id, like leave)
else                                                          -> ''      (blank)
```

- Batched: add one query each for active `WorkingDay` dates and overlapping `WFHRecord`s in
  the range (alongside the existing attendance/leave/holiday queries) — still ~6 bulk queries.
- **Column shading & weekend cells** must exclude `WorkingDay` dates (a working Saturday column
  is not shaded and its cells aren't locked).
- `wfh_record_id` is carried on a cell only for single-day WFH records (so the matrix could
  offer a remove toggle later — Phase 2 may reuse the leave toggle pattern; see Out of scope).

### UI

**Attendance Settings page** — add the `expected_in_by` time field to the existing form.

**WorkingDay CRUD** — list/create/update/delete views + templates mirroring the existing
`Holiday` CRUD, plus a nav link next to Holidays (under HR → Leave or a new "Calendar" group).

**WFH multi-day form** — a `WFHRecordCreateView` (employee, start_date, end_date, note) + a
list, mirroring `LeaveRecordCreateView`; nav link. Deleting a WFHRecord is supported.

**Daily grid — per-day WFH:** add a **WFH** button per row beside Present/Absent. To preserve
the grid's single bulk **Save all** model, each row carries a hidden `wfh_<pk>` flag:
- Clicking **WFH** sets `wfh_<pk>=1`, fills the default times (08:15/18:00, editable, so hours
  count), and flips the badge to WFH. Clicking **Present**/**Absent** clears the flag.
- On **Save all**, for each active employee: if `wfh_<pk>` is set → `get_or_create` a **1-day**
  `WFHRecord(employee, day, day)`; else → delete any **1-day** `WFHRecord` for that
  (employee, day) (never a multi-day one). Then derive status (WFH-covered days resolve to
  'wfh') and upsert the AttendanceRecord with the entered times.
- Locked rows (leave/holiday/weekend that isn't a WorkingDay) keep no buttons.

**Legends & badges** — grid, matrix, and history get the LT (orange) and RM (purple)
entries; the holiday amber stays.

**Monthly summary** (`AttendanceHistoryView`) — add `late` and `wfh` to the status counts.

## Build phases (one spec, two implementation phases)

- **Phase 1 — Late + Working-day exceptions:** `expected_in_by` setting + late derivation;
  `WorkingDay` model + CRUD + nav; weekend-override in `derive_status` and `build_matrix`
  (incl. column shading); badges/legends for Late. Contained settings/calendar work.
- **Phase 2 — WFH:** `WFHRecord` model + multi-day form/list/delete + nav; the grid WFH button
  with per-row flag and bulk-save reconciliation; WFH in `derive_status` and `build_matrix`;
  badges/legends for WFH; summary count.

Phase 2 depends on Phase 1 (shares the derivation/matrix changes and badge work).

## Edge cases

- **WorkingDay on a non-weekend date:** no effect (the weekday is already a working day).
- **Late threshold exactly equal to check-in:** `check_in > expected_in_by` is strict, so a
  check-in equal to the threshold is **Present**, not Late.
- **WFH on a weekend day (not a WorkingDay):** shows Weekend (weekend precedes WFH), consistent
  with `derive_status`.
- **Grid WFH toggle vs a multi-day WFHRecord:** the bulk-save reconciliation only creates/deletes
  **single-day** WFHRecords (start==end==day); a multi-day record is never touched by the grid.
- **Changing `expected_in_by` after records exist:** stored `late`/`present` statuses go stale
  until the day is re-saved or the existing "Regenerate statuses" action is run (same caveat as
  holidays/weekend changes today).

## Testing

- Late: check-in before/at/after `expected_in_by` → present/present/late; hours still computed.
- WorkingDay: a Saturday in WorkingDay → not weekend (present/absent/late apply); matrix column
  not shaded; a WorkingDay on a weekday is a no-op.
- WFHRecord: covers a day → 'wfh' in `derive_status` and `build_matrix`; hours from times.
- Grid WFH button: bulk save creates a 1-day WFHRecord + 'wfh' AttendanceRecord; un-flagging then
  saving deletes the 1-day record and reverts to present/absent; multi-day records untouched.
- `derive_status` full precedence ordering across all 7 statuses.
- Monthly summary includes late + wfh counts.
- Migrations: each new model/field is one migration; no churn to existing models.

## Out of scope (this spec)

- Per-employee or per-role late thresholds (single global `expected_in_by`).
- WFH leave-style balance/quota (WFH is unlimited worked time).
- A matrix click-to-mark WFH toggle (Phase 2 adds WFH via the grid + the multi-day form; a
  matrix WFH toggle mirroring the leave toggle is a later enhancement).
- Overtime, half-day, break deductions, biometric integration (already project-wide out of scope).
