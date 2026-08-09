# Multi-Step Leave Approval Workflow — Design

## Context

The HR module's leave system (`hr/models/leave.py`) currently treats leave as: `LeaveType` (defines allowance),
`LeaveEntitlement` (per-employee/year allowance total), `LeaveRecord` (an actual taken-leave entry, created directly
by an admin via `LeaveRecordCreateView`, immediately counting against the balance). There is no request/approval
step — an admin recording a `LeaveRecord` *is* the approval.

This spec adds a request/approval layer **only for conditional (non-Annual) leave types**. Annual leave keeps the
existing direct-entry flow untouched.

## Scope

- Task 1: update `LeaveType` default values and flip `is_accumulative` so only `code='annual'` is `True`.
- Task 2: remove the "Counts in Summary" column from the leave-types list template.
- Task 3: tweak column labels + keep the explanatory note on `/hr/leave/entitlements/`.
- **Task 4 (this design's focus): new dual-approval request workflow for conditional leave.**
- Task 5: reflect new statuses/notes on My Profile and other HR views that show leave.
- Task 6: seed data (employee link for current superuser, Aamna Khan + Ali Sultan as designated approvers, and
  sample pending/approved/disapproved requests with notes + a dummy document).

## Data Model (new, all in `hr` app)

### `LeaveApprover`
Designates who has authority to approve/reject conditional leave requests. This is the "robust, not hardcoded"
mechanism the access-control requirement calls for — approval authority is a DB fact, not a username check.

- `user` — OneToOne → `settings.AUTH_USER_MODEL`
- `is_active` — bool, default True (deactivating here revokes approval authority without deleting history)
- `created_at`

### `LeaveRequest`
The thing an employee submits (or an admin logs on their behalf) for a conditional leave type.

- `employee` — FK → `Employee`
- `leave_type` — FK → `LeaveType` (must be non-accumulative; enforced in `clean()`)
- `start_date`, `end_date` — dates; `clean()` rejects `end_date < start_date` (same convention as `LeaveRecord`)
- `days` — decimal, auto-computed from the date range if left blank (mirrors `LeaveRecord` behavior)
- `employee_reason` — text, optional
- `document` — single `FileField`, uploaded to a per-employee path; **never exposed via a public media URL** —
  served only through an authenticated download view (see Security below)
- `status` — `pending` / `approved` / `disapproved` / `cancelled`
- `created_by` — FK → User (the employee themself, or an admin logging on their behalf)
- `leave_record` — nullable OneToOne → `LeaveRecord`; populated only once fully approved — this is what actually
  deducts from the balance
- `salary_deduction_applicable` — bool, default False; auto-set True when disapproved
- `salary_deduction_note` — text, optional (lets an admin waive/explain the deduction)
- `is_overridden`, `overridden_by` (FK User, nullable), `override_reason` (text) — superadmin deadlock-breaker
- `decided_at`, `created_at`, `updated_at`

### `LeaveRequestApproval`
One row per designated approver, **snapshotted at submission time** from the currently-active `LeaveApprover` set
(so later roster changes don't rewrite history of past requests).

- `leave_request` — FK
- `approver` — FK → User
- `decision` — `pending` / `approved` / `disapproved` / `skipped` (`skipped` is set on the remaining rows when a
  superadmin override finalizes the request first)
- `comment` — text, optional (shown alongside the decision)
- `decided_at`

### `LeaveRequestNote`
- `leave_request` — FK
- `author` — FK → User
- `note` — text
- `is_internal` — bool, default False. When True, hidden from the employee (admin-only note) — a small proactive
  addition beyond the literal ask, so admins have somewhere to coordinate without every note being employee-facing.
- `created_at`

## Approval / Finalization Logic

A single service function (`hr/leave_approval_services.py`), called after any approver records a decision or an
override happens:

1. Any one approver `disapproved` → request is immediately `disapproved` (fail-fast; one rejection is decisive,
   consistent with the task's framing). `salary_deduction_applicable = True`. Remaining `pending` approval rows are
   left as-is (they simply never got to act — this is not an override, so they aren't marked `skipped`).
2. All approvers `approved` → request becomes `approved`; a real `LeaveRecord` is created from the request's
   employee/leave_type/dates/days and linked back via `leave_request.leave_record`. This is the only path that
   actually touches the employee's balance.
3. Otherwise (some still `pending`, none disapproved) → stays `pending`.

**Superadmin override** (deadlock prevention — e.g. a designated approver is on leave/unavailable): any
`is_super_admin_user` can call a distinct override action with a mandatory `reason`. This sets `is_overridden=True`,
`overridden_by`, `override_reason`, marks any still-`pending` `LeaveRequestApproval` rows as `skipped`, and drives
the same approve/disapprove side effects as normal finalization (balance deduction or salary-deduction flag). The
override is always labeled distinctly in the UI ("Overridden by {admin} — {reason}") so it's never confused with a
genuine dual-approval.

## Access Control

Two layers, matching the task's own phrasing:

- **View access** (seeing the queue at all): gated on `request.user.is_super_admin_user` — a real role check.
- **Act access** (approve/reject buttons rendered and enabled): only for a user with an active `LeaveApprover` row
  for themselves. A super admin without a `LeaveApprover` row can view the queue (transparency) but sees no
  approve/reject buttons on requests — only the Override action.

This is what "restricted to Super Admins, specifically Aamna Khan and Ali Sultan" cashes out to without hardcoding
names anywhere in view code: the *page* is role-gated, the *approval authority* is roster-gated.

## UI

- **Queue** (`/hr/leave-requests/`, super-admin only): FIFO list — pending requests first (oldest first), then a
  collapsed/paginated history of approved/disapproved ones. Each row shows employee, type, dates, status badge,
  and (for pending) who's still holding it up.
- **Detail** (`/hr/leave-requests/<pk>/`): employee + dates + type + document (secure download link) + the two
  approvers' decisions (with comments) + notes thread (add note form) + Approve/Reject buttons (if the viewer is a
  designated approver and hasn't decided yet) + Override section (if super admin and request still pending).
- **My Profile**: existing "Leave Balance" card gets a new "My Leave Requests" list beneath it showing status per
  request ("Pending — waiting on Ali Sultan", "Approved", "Disapproved — salary deduction applies", etc.), plus a
  "Request Leave" button opening a small self-service form (type, dates, reason, optional document) for any
  non-Annual type.
- **Admin logging on behalf of an employee**: a "Log Request" action on the queue page, same form, with an
  employee picker, submitted `created_by=admin`.
- **In-app notifications** (added feature, using the existing `notifications.services.notify_users` — no new
  infrastructure): the employee is notified on submission ("Request submitted"), on final approval/disapproval, and
  the *other* approver is notified when one approver has already decided ("Marriage leave for X is awaiting your
  approval — Aamna Khan already approved it") so nothing sits pending purely from lack of awareness.

## Security Notes

- `LeaveRequest.document` is uploaded to a path namespaced by employee id, and served exclusively via
  `leave_request_document_download` — a view that 404s/403s unless the requester is the request's own employee, a
  `LeaveApprover`, or a super admin. No template ever renders `document.url` directly.
- This is a deliberate departure from the existing `EmployeeDocument`/`medical_certificate` pattern (which link
  straight to the public media URL) — flagged as an out-of-scope observation in the final report, not silently
  "fixed" elsewhere, since that's pre-existing behavior outside this task's scope.

## Out of Scope / Explicitly Not Doing

- Not touching `LeaveRecordCreateView` or the Annual leave flow.
- Not retrofitting the existing `EmployeeDocument`/`medical_certificate` file fields with the same protected-download
  pattern (flagged as an observation instead).
- Not adding email notifications for approval events — only in-app notifications via the existing
  `notify_users` service (no email-sending path exists in that service for this kind of event, and adding one is
  out of scope here).
- Not supporting more than one document per request (per explicit answer).
