# Annual Leave — Location Baselines & HR Exception Overrides — Design

## Context

Today, `LeaveType.default_annual_days` is a single flat number (e.g. 30) copied onto every employee's
`LeaveEntitlement.entitled_days` regardless of where they work, via `generate_entitlements_for_employee()`
and `reapply_leave_type_defaults()` (`hr/leave_services.py`). Submission is validated by
`validate_leave_submission()` (`hr/models/leave.py`), which hard-blocks any request exceeding
`entitled_days` — identically for self-service, the admin "log on behalf of" form, and the legacy Add
Leave form, since all three now funnel through the single `submit_leave_request()` entry point
(`hr/leave_approval_services.py`).

`Employee.work_location` (`'office'` / `'site'`, `hr/models/employee.py`) already exists but is not
consulted anywhere in the leave-entitlement pipeline.

This spec adds:
1. A per-work-location standard baseline (Office 30 / Site 45, both editable, not hardcoded).
2. A standing, HR-granted "exception days" bank on top of an employee's baseline, with a full audit trail.
3. A configurable escape hatch that lets a Super Admin push a specific over-cap request through anyway.

## Scope

- Applies to Annual Leave initially, but the mechanism is generic per `LeaveType` — any leave type can
  opt into location-differentiated defaults.
- The "hold instead of hard-block" submission behavior (below) initially applies **only to Site
  employees**; Office employees keep today's exact hard-block behavior. This is a setting, not a
  hardcoded rule — a Super Admin can extend it to Office later with no code change.
- Out of scope: anything about `AttendanceException` (a separate, unrelated workflow — managers approve
  those today; they have never approved Leave and continue not to).

## Data model changes

### `LeaveType` (`hr/models/leave.py`)
- Add `site_default_annual_days` — `DecimalField(null=True, blank=True)`. Blank means "same as
  `default_annual_days`" (today's behavior, untouched) — so every leave type other than Annual needs zero
  configuration change.
- `LeaveType.save()`'s existing propagate-on-change behavior (`self.entitlements.update(entitled_days=...)`)
  becomes location-aware: Office employees' entitlements get `default_annual_days`; Site employees' get
  `site_default_annual_days` (or `default_annual_days` if blank). It must **not** touch `exception_days`
  (see below) — that lives outside the field being overwritten, so re-applying defaults can never wipe out
  an HR grant.

### `LeaveEntitlement` (`hr/models/leave.py`)
- `entitled_days` keeps its current meaning exactly: the **standard baseline only**. This is what anchors
  the employee's dashboard number — it must never be inflated by an exception grant.
- New computed property `exception_days` — sums this employee/leave_type/year's `LeaveExceptionGrant` rows
  (see below). Not a stored field, matching how `taken_days`/`remaining_days` are already computed here,
  not stored.
- New computed property `effective_entitled_days` = `entitled_days + exception_days`.
- New computed property `effective_remaining_days` = `effective_entitled_days - taken_days`. This is the
  number validation actually checks against. The existing `remaining_days` property (baseline only) keeps
  its current meaning and stays the headline number on the employee's dashboard.

### New model `LeaveExceptionGrant`
One row per HR grant action — an audit log, not a single overwritable counter, matching this codebase's
existing discipline around `override_reason`/`overridden_by`/`is_overridden`:
- `employee` (FK), `leave_type` (FK), `year` (PositiveIntegerField)
- `days` (DecimalField) — the amount granted (e.g. `+5`)
- `granted_by` (FK to user, `SET_NULL`), `granted_at` (auto_now_add)
- `reason` (TextField, required at the form/service layer)

### `LeaveRequest` (`hr/models/leave.py`)
- Add `exceeds_balance` (`BooleanField(default=False)`) — set at creation time for a request that was held
  for override rather than hard-blocked. No other new fields: the actual override decision reuses the
  existing `is_overridden` / `overridden_by` / `override_reason` fields and `override_finalize()` service
  function unchanged.

### Settings (extends the existing `OverrideAccessSettings` "solo" model)
- `allow_site_balance_hold` (`BooleanField(default=True)`)
- `allow_office_balance_hold` (`BooleanField(default=False)`)

Both are editable from the same Org Chart → "Leave Dashboard Access" tab that already configures who holds
override rights — no new settings screen, and no code change needed to extend this to Office later.

## Business logic changes

### Submission validation (`validate_leave_submission`, `hr/leave_services.py`)
Checks against `effective_remaining_days` (baseline + exception) minus already-pending days, not just
baseline, for both self-service and HR-logged submissions — an employee's own future self-service requests
must be able to draw on an exception grant HR already made, not just have it show up as a number.

When a request exceeds the effective available balance:
- If `employee.work_location` is in the settings' hold-enabled set (Site, by default): **do not raise**.
  The request is created normally via `submit_leave_request`, with `exceeds_balance=True`, and — critically
  — **no `LeaveRequestApproval` roster rows are created for it**. The normal approver roster
  (`LeaveDashboardAccess`) only ever decides in-cap requests; a balance-exceeding request is invisible to
  their normal decide action and simply sits `pending` until a Super Admin with override access acts on it
  via the existing `override_finalize()` path (same mandatory-reason, audited mechanism already used for
  approver-unavailable overrides).
- If the employee's `work_location` is not in the hold-enabled set (Office, by default): raises exactly as
  today — same error message, same behavior, no change for Office employees until a Super Admin flips the
  setting.

This is one shared branch in one shared function — self-service and "log on behalf of" get identical
treatment based purely on the employee's `work_location`, not on who is submitting. The only thing that
differs between the two screens is what the *submitting* Super Admin sees (below).

A held (`exceeds_balance=True`) request still shows as "Pending" to the employee in their own leave list —
but with a distinct sub-label ("Pending — awaiting HR review") so they aren't left thinking their normal
approver simply hasn't gotten to it yet.

### `Employee.work_location` change mid-year
On `Employee.save()`, if `work_location` changed, recompute the current year's Annual `entitled_days` from
the new baseline immediately (retroactive for the whole year, confirmed) — `exception_days` is untouched
since it's a separate computed source.

Edge case this creates on a **downgrade** (Site → Office) where the employee already took more than the
new, lower baseline allows: rather than surfacing a confusing negative `remaining_days` for leave they
validly took under the old baseline, auto-create a `LeaveExceptionGrant` for exactly the shortfall, with a
system-generated reason ("Auto-preserved N day(s) already taken under Site baseline before transfer to
Office on {date}"). This keeps the audit trail honest (the grant is visible, dated, explained) without
punishing the employee for a location change they didn't control.

## Permissions

No new permission concept. Exception grants, the balance-hold settings, and the override action are all
gated by the existing `has_override_access()` / `OverrideAccessSettings` mechanism (already configurable
from the Org Chart page, defaults to all Super Admins) — this is the "HR can reconfigure this without
touching code" answer for all three capabilities at once.

## UI/UX

### Employee dashboard (`templates/hr/leave_summary.html`, `entitlement_year.html`)
Baseline stays the headline; an exception grant is a clearly separate, additional line — never blended
into the anchor number:
```
Annual Leave — 45 days standard · 3 taken · 42 remaining
+ 5 exception days granted (Jul 2026) · 0 used · 5 available
```

### HR "Add Exception Days" (new, on the employee profile)
Small form: leave type (defaults Annual), year, `+N days`, reason (required). Creates one
`LeaveExceptionGrant` row. Gated by override access.

### Where the override actually happens: the existing Leave Requests queue/detail page
No new dialog on the log-on-behalf-of form. `LeaveRequestListView`/`LeaveRequestDetailView`
(`hr/views.py:2037`, `2745`) already gate their Override control purely on `has_override_access(user)` and
`status == 'pending'` — with no dependency on approval rows existing. A held request (created with zero
`LeaveRequestApproval` rows) therefore already appears in the queue and already has a working Override
button, with zero changes to that gating logic: `is_approver`/`my_approval` are naturally empty for a held
request, so the normal decide control correctly never renders for anyone — nothing to build there.

What's added: a red "Exceeds balance by N day(s)" badge/line on the list row and detail page whenever
`exceeds_balance=True`, showing the breakdown (standard / exception / taken), so HR understands *why*
they're being asked to override before they click it. The "log on behalf of" form's success message
distinguishes the two outcomes ("sent for approval" vs "logged — exceeds balance, needs Super Admin
review") so the submitting HR user isn't left thinking it went through normally.

### Sidebar pending-count indicators (new)
Reuses the existing `.notif-badge` visual language (`var(--leap-red)`, white bold text, 18px pill, hidden
entirely at 0, capped "99+") for numbered counts, plus a new smaller `.nav-badge-dot` (red, no number) for
collapsed parent items:
```
Leave  ▾                              ●   <- dot: something pending inside
  Leave Requests              (3)         <- real count
  ...

My Profile  ▾                         ●   <- dot: something pending inside
  My Profile
  Team Exceptions             (5)         <- real count
```
Both parent/child pairs get the same treatment for consistency (the user asked explicitly for My
Profile/Team Exceptions; Leave/Leave Requests is the identical structural problem, added for symmetry).

Team Exceptions' 3 tabs each get the same numbered pill next to their label, scoped to that tab's own
queryset (`direct` / `secondary` / `all`), hidden at 0:
```
[ Direct Reports (2) ] [ Secondary Reports (1) ] [ All Organization Requests (5) ]
```
Badge color is consistently `--leap-red` everywhere (not each tab's own identity color) — the badge means
"needs action," which should read the same regardless of which tab it's on; the tabs' existing red/teal/
slate colors remain their own identity, not overloaded as a severity signal.

All counts are computed once per request by a new `hr` context processor, reusing the *exact* querysets the
real pages already use (`TeamExceptionsView._tab_queryset(...)`, `LeaveRequestListView`'s pending scope) so
the sidebar can never drift from what the page itself shows. It only runs for users who'd actually see
these nav items (`can_view_team_exceptions()` / `is_super_admin_user or leave_dashboard_access.is_active`);
everyone else pays zero extra queries. Rendered server-side per page load — not polled live like the
notification bell, since these change far less often and every navigation already refreshes them.

## Testing

- `LeaveType`/`LeaveEntitlement` generation: Office vs Site baseline picked correctly; blank
  `site_default_annual_days` falls back to `default_annual_days`.
- `LeaveExceptionGrant`: effective cap includes granted days; self-service can submit against it.
- Submission validation: Site employee held-not-blocked over cap creates a request with no approver rows;
  Office employee still hard-blocked; override decision approves/rejects a held request via
  `override_finalize`.
- `reapply_leave_type_defaults()`: location-aware, and never touches `LeaveExceptionGrant` rows.
- Work-location transfer: upgrade recomputes baseline for the full year; downgrade auto-grants the exact
  shortfall with a system reason.
- Sidebar/tab badge counts match the counts the underlying pages themselves compute, for a scoped user
  (manager) and an unscoped user (Super Admin).
- Per this team's process discipline: run the **full** `manage.py test` suite before considering any task
  done, not just `hr` — a past refactor broke an unrelated seed command that its own feature tests never
  touched.
