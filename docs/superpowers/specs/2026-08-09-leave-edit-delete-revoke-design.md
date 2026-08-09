# Leave Request / Team Exception Edit, Delete & Revoke — Design

## Context

Today, once a `LeaveRequest` or `AttendanceException` is submitted, its creator has no way to fix a mistake
or withdraw it — the only path is asking an approver to disapprove it. And once approved, there is no way
to undo it at all: an employee whose approved leave falls through (project emergency, etc.) has no
mechanism to give the days back, and HR has no way to void an approved-then-no-longer-valid request without
leaving a wrong balance on the books forever.

This spec adds two independent capabilities to **both** Leave Requests and Team Exceptions:

1. **Edit/Delete while pending and undecided** — the creator can fix or withdraw their own submission,
   but only before anyone has acted on it.
2. **Revoke** — an approved request can be voided after the fact, either directly by someone with override
   access, or requested by the employee and approved by the same people who decide normal requests. The
   record stays visible in history either way — revoke is explicitly not delete.

## Global decisions (confirmed)

- **Edit/delete lock condition (Leave Requests):** locks the moment **any** approver has recorded **any**
  decision — approved or disapproved. A disapproval is a real decision; the creator must not be able to
  edit it away.
- **Who decides a revoke request:** the same roster that already decides normal requests for that
  workflow — `LeaveDashboardAccess` holders + Super Admins for Leave Requests. For Team Exceptions (whose
  *normal* decision-maker is the employee's assigned manager, not a fixed roster), the natural extension of
  the same principle is the assigned manager, with override-access holders as the existing fallback —
  **flagging this as my own extrapolation**, not something explicitly confirmed; correct me if Team
  Exception revoke-requests should instead go straight to override-access holders only, skipping the
  manager.
- **Revoke mechanics (Leave):** delete the linked `LeaveRecord`. `taken_days`/`remaining_days` already sum
  `LeaveRecord` rows live, so the day(s) become available again with no new calculation logic — the
  `LeaveRequest` itself stays, re-labeled `status='revoked'`.
- **Revoke mechanics (Team Exceptions):** no linked record to delete — approval there is read live by the
  attendance-status logic, so revoking is just a status change (`status='revoked'`) that the read path
  needs to treat as "no longer excused."

## Data model changes

### `LeaveRequest`
- `STATUS_CHOICES` gains `('revoked', 'Revoked')`.
- New fields: `revoked_by` (FK user, `SET_NULL`), `revoked_at` (DateTimeField), `revoke_reason` (TextField).
- No new field needed for the edit/delete lock — it's derived from `approvals.exclude(decision='pending').exists()`, computed, not stored.

### New model `LeaveRevokeRequest`
One row per revoke request (an approved `LeaveRequest` can have at most one *active* — pending — revoke
request at a time, enforced at the service layer, not a DB constraint, since a rejected one shouldn't block
trying again later):
- `leave_request` (FK, `related_name='revoke_requests'`)
- `requested_by` (FK user) — always the employee themselves (self-initiated only, per spec; HR/Super Admin
  use the *direct* revoke action instead, not this queue)
- `reason` (TextField, required)
- `status` (`pending` / `approved` / `rejected`)
- `decided_by` (FK user, null), `decided_at` (DateTimeField, null), `decision_note` (TextField) — required
  when rejecting (explains why the revoke isn't happening); optional when approving (the original `reason`
  already explains why), matching this codebase's existing "write down why when it's not obvious" pattern
  rather than demanding a redundant note every time.
- `created_at` (auto_now_add)

### `AttendanceException`
- `STATUS_CHOICES` gains `('revoked', 'Revoked')`.
- New fields: `revoked_by`, `revoked_at`, `revoke_reason` — same shape as `LeaveRequest`.
- The attendance-status derivation logic (`hr/attendance_services.py`'s `derive_status` or equivalent) must
  treat `status='revoked'` the same as **not approved** when deciding whether to excuse a late/absent mark
  — this is the one piece of *existing* read-path logic that needs to learn about the new status; everywhere
  else, `revoked` just needs to render distinctly from `approved`/`rejected`/`pending`/`expired`.

### New model `AttendanceExceptionRevokeRequest`
Same shape as `LeaveRevokeRequest`, pointed at `AttendanceException` instead. Two small parallel models
rather than one generic one — the two source models differ enough (multi- vs single-approver, linked
record vs none) that a shared generic-relation model would need almost as much special-casing per type as
just having two models, for no real savings.

## Business logic

### Edit/Delete (Leave Requests)
- Gate: `request.user == leave_request.created_by` AND `leave_request.status == 'pending'` AND
  `not leave_request.approvals.exclude(decision='pending').exists()`.
- **Delete:** removes the `LeaveRequest` and its `LeaveRequestApproval` rows (cascade) — no `LeaveRecord`
  exists yet at this stage (only created on approval), so nothing else to clean up.
- **Edit:** re-opens the same `LeaveRequestForm` pre-filled, re-running the exact same
  `validate_leave_submission`/balance-hold logic a fresh submission would — an edited request is not
  exempt from the balance rules that applied to the original one. Editing does **not** reset the approver
  roster (the same `LeaveDashboardAccess` snapshot from submission stays) — only whoever already declined
  to decide (still `pending`) sees the edited version next; this is consistent with "locks the moment any
  approver decides" — if nobody has decided yet, there's nothing to invalidate.

### Edit/Delete (Team Exceptions)
- Gate: `request.user == exc.created_by` AND `exc.status == 'pending'` (deliberately **not** `'expired'` —
  an exception that already blew its decision window is a different situation than one still fresh;
  flagging this as an assumption, easy to widen if you want expired ones editable too).
- Delete/edit otherwise mirror the Leave Request case, adapted to the single-decision model (no approvals
  table to check — `status == 'pending'` alone is sufficient, since any decision immediately moves status
  off `'pending'`).

### Direct revoke (Super Admin / override access)
- Gate: `has_override_access(request.user)` (the exact same permission already gating the balance-override
  "Log Anyway" action — no new permission concept) AND `status == 'approved'`.
- UI: a "Revoke" button on the request's detail page → confirmation dialog → mandatory reason field →
  submit. Sets `status='revoked'`, `revoked_by`, `revoked_at`, `revoke_reason`; for Leave Requests, deletes
  the linked `LeaveRecord`.
- Notifies the employee (mirrors the existing approve/disapprove notification pattern).

### Employee-requested revoke
- Employee sees a "Request Revoke" button on their own **approved** request (My Profile → My Leave
  Requests / My Attendance Exceptions), opens a small form: reason (required), submit → creates a
  `pending` `*RevokeRequest` row. The original request's `status` stays `'approved'` until decided — no
  UI change to it yet beyond a small "Revoke requested — awaiting review" note.
- The decision roster (Leave: `LeaveDashboardAccess` + Super Admins; Team Exceptions: the assigned manager
  + override-access holders) sees pending revoke requests in a small new section on their existing queue
  page — **not** a separate new page, to avoid adding another place people have to remember to check.
  Approve → applies the same revoke mechanic as the direct path (tagged as having originated from the
  employee's own request, for the history view). Reject → the original request is untouched, the
  `RevokeRequest` row is marked `rejected` with the decision note.

## UI/UX (kept deliberately minimal, per "not cluttered")

- **Pending, undecided, mine:** small "Edit" / "Delete" icon-buttons next to the existing row, only
  rendered when the gate passes — nobody else ever sees them on someone else's row.
- **Approved:** a single "Request Revoke" button (employee) or "Revoke" button (override-access holder) —
  never both visible to the same viewer at once, since an employee viewing their own request never has
  override access to themselves-affecting decisions by definition of the existing self-approval rules.
- **Revoked:** renders as its own distinct badge (e.g. slate/gray, not red like disapproved, not green like
  approved) with the reason and who/when shown inline — same "always show the why" convention as
  Overridden/Disapproved already use. **Must appear in both histories, not just one:** the employee's own
  side (My Profile → My Leave Requests / My Attendance Exceptions) AND the decider's side (Leave Approval
  Queue's History card / Team Exceptions' History card) — mirroring how `status='disapproved'` and
  `is_overridden` already render symmetrically in both places today (e.g. `my_profile.html`'s "My Leave
  Requests" table and `leave_request_list.html`'s History card both show the same Disapproved badge +
  decided-by attribution off the same underlying fields). The Revoked badge is driven by the same
  `status='revoked'` field read from both templates, so this falls out naturally as long as both templates'
  status-badge blocks add a `revoked` branch — call this out explicitly in the implementation plan so
  neither template's branch gets missed.
- **Pending revoke requests:** a small badge count next to the relevant queue link (reusing the exact same
  sidebar-badge mechanism already built for Leave Requests/Team Exceptions pending counts), so this new
  queue doesn't get forgotten.

## Edge cases

| Case | Resolution |
|---|---|
| Creator tries to edit/delete after ANY approver decided | Blocked — gate checks `approvals.exclude(decision='pending').exists()`, not just full approval. |
| Two approvers, one approved one still pending | Locked (matches the confirmed "any decision locks" rule) — the approved approver's decision must not be edited out from under them. |
| Employee submits a second revoke request while one is already pending | Blocked at the service layer — at most one *active* (`pending`) revoke request per `LeaveRequest`/`AttendanceException`. |
| Revoke request for something not currently `approved` (e.g. already revoked, or somehow reverted) | Rejected at the service layer with a clear error — revoke only ever applies to `approved`. |
| Direct revoke races with an in-flight employee revoke-request | Direct revoke wins immediately (Super Admin authority is always final) and the pending `RevokeRequest` is auto-marked `approved`/closed with a system note, rather than left dangling pointing at an already-revoked request. |
| Self-revoke-decision | An override-access holder deciding their own revoke request on their own leave is blocked — same self-approval-prevention principle already enforced everywhere else in this codebase (`is_own_request` checks). |
| Deleting a pending request that has a document attached | The uploaded file is deleted along with the row (matches Django's default `FileField` behavior on model delete — no special handling needed unless you want the file retained, which nothing here requires). |
| Editing changes the balance situation (now exceeds cap) | Re-runs `validate_leave_submission` exactly as a fresh submission would — could newly become `exceeds_balance=True` and get held, exactly like any other submission. |

## Testing

- Edit/delete gates: creator-only, pending-only, locks on any recorded decision (approved and
  disapproved cases both), for both Leave Requests and Team Exceptions.
- Direct revoke: override-access-only, approved-only, `LeaveRecord` deleted, balance recalculates
  correctly, status/badges render correctly, non-override users never see the button.
- Revoked status visibility: after a revoke, the "Revoked" badge (with reason and who/when) renders on
  **both** the requester's own History (My Profile) and the decider's queue History (Leave Approval Queue /
  Team Exceptions) — a test per side, for both Leave Requests and Team Exceptions.
- Employee revoke-request: creation, duplicate-pending-request blocking, approval applies the revoke,
  rejection leaves the original untouched, self-decision blocked.
- Team Exceptions: same coverage, adapted to the single-decision/no-linked-record model.
- Sidebar/queue badge counts include pending revoke requests correctly.
