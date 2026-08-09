# Manager-Logged Leave Requests (Org-Chart-Linked) — Design

## Context

Today, "who can log a leave request on someone else's behalf" is governed by
`hr/scoping.py`'s `scoped_employee_ids()` — a **Role**-based check. Only three
named Roles (`site_manager`, `project_manager`, `document_controller`) plus
the company-wide admin tiers get this power. An employee's actual
`main_manager` assignment in the Org Chart confers nothing by itself: if
Ahmed is set as Sarah's manager but doesn't hold one of those Roles, Ahmed
cannot log leave for Sarah today.

This spec adds that missing link — a manager's power to log leave for their
**direct reports** derives from the live Org Chart relationship
(`Employee.main_manager`), the same way `can_view_team_exceptions()`
(`hr/views.py:115`) already derives Attendance Exception visibility from
`main_reports`/`secondary_reports`, rather than from a Role.

## Scope

- A manager (anyone who is another active employee's `main_manager`) can log
  a leave request for that direct report, via the existing "Log Request"
  form (`LeaveRequestCreateView`, `hr:leave_request_create`).
- **Direct reports only** — not the whole downstream subtree. A report's own
  reports still need their own direct manager, HR, or an admin tier to log
  for them.
- **One employee per submission** — the existing single-employee form,
  reused as-is, not a new bulk/multi-select flow.
- A manager-logged request goes through **the exact same approval queue** as
  any other request (`LeaveDashboardAccess` roster — "Waiting on: Ali,
  Aamna"). Logging is not approving; a manager gets no auto-approve power.
- Secondary managers get **visibility only** (they can see the request once
  it exists, same as they already can via Team Exceptions-style scoping),
  not the ability to originate a log themselves — matches the existing
  asymmetric treatment of secondary managers in `can_decide_attendance_exception`.
- Explicitly **out of scope**: this does not touch `scoped_employee_ids()`
  or `scope_asset_queryset()` — a manager's new leave-logging power does not
  grant them any new visibility into their reports' assets, attendance
  records, or anything else. If broader org-chart-driven HR visibility is
  wanted later, that's a separate, deliberate follow-up.

## Design

### New access check

A new function, alongside `can_view_team_exceptions` in `hr/views.py`:

```python
def is_direct_manager_of(user, employee):
    """True if `user` is `employee`'s live main_manager. Direct only —
    deliberately does not walk the downstream subtree. Mirrors
    can_view_team_exceptions' use of the live main_reports relationship
    rather than a Role or a snapshot."""
    emp = getattr(user, 'employee_profile', None)
    if emp is None:
        return False
    return bool(employee.is_active and employee.main_manager_id == emp.id)


def can_log_leave_for(user, employee):
    """Access gate for logging a leave request on someone else's behalf —
    the existing Role-based scoping (HR/admin tiers, site/project_manager,
    document_controller) OR being that employee's direct manager."""
    ids = scoped_employee_ids(user)
    if ids is None or employee.pk in ids:
        return True
    return is_direct_manager_of(user, employee)
```

### `LeaveRequestCreateView` changes

- Permission gate widens from `HRScopedAccessMixin` alone to also admit any
  user who is `main_manager` for at least one active employee (a new
  `test_func`-style check, or a widened mixin — implementation detail for
  the plan).
- `get_form`'s employee-dropdown scoping widens from
  `scoped_employee_ids(user)` to also include `emp.main_reports.filter(is_active=True)`
  when the user isn't already covered by the existing Role-based scope —
  so a plain manager's dropdown shows exactly their direct reports, nothing
  more.
- No changes to `form_valid`, balance handling, the inline "exceeds balance"
  warning, or the Super-Admin-only "Log Anyway" grant path — a manager
  submitting an over-cap request for a direct report gets precisely the
  same held/warned/normally-routed treatment any other non-Super-Admin
  submitter gets today. Nothing new to build there.

### Discoverability

A manager with no other HR-facing role currently has no reason to ever find
`/hr/leave-requests/create/`. **My Profile already has a "My Reporting
Structure" card listing Direct Reports by name** — add the **"Log Leave"**
link directly to each active report's row there (opening the existing form
pre-filled via its already-supported `?employee=<id>` query parameter)
instead of introducing a new, separate section. No new form, no new card —
just a new control inside the section that already exists for this exact
purpose.

## Edge cases and how each is resolved

| Case | Resolution |
|---|---|
| Manager logs leave for themselves | `can_log_leave_for` is never checked against the caller's own record for this purpose — the dropdown never includes the manager's own row (`main_reports` excludes self by definition; a manager is never their own report). |
| Manager logs leave for their own manager (upward abuse) | `is_direct_manager_of` only ever checks `employee.main_manager_id == emp.id` — structurally cannot be satisfied upward. |
| Manager reassigned mid-flight | Checked live at request time via `employee.main_manager_id`, never cached/snapshotted — matches `can_decide_attendance_exception`'s documented rationale exactly. |
| Report's report (two levels down) | Out of scope by design — `is_direct_manager_of` does not walk `get_downstream_employee_ids`. Needs the direct manager, an admin tier, or one of the existing whole-subtree Roles. |
| Secondary manager tries to log | Not covered by `is_direct_manager_of` (which checks `main_manager_id` specifically) — secondary managers get visibility, not logging power, matching the existing attendance-exception asymmetry. |
| Inactive employee | `is_direct_manager_of` requires `employee.is_active`; `main_reports.filter(is_active=True)` in the dropdown scoping. |
| Employee with no manager assigned (root node) | Nobody gets manager-power over them; only the existing Role/admin tiers can log for them — unchanged from today. |
| Cycle in the manager chain | Already prevented at the data layer — `Employee.clean()` blocks assigning a `main_manager` that would create a loop. Nothing new needed. |
| Manager-logged request silently auto-approves | Explicitly rejected — always goes through `submit_leave_request()`'s normal approver-roster assignment, unchanged. |
| Over-balance request logged by a manager | Same held/flagged/normally-routed treatment as any non-Super-Admin submitter; the "Log Anyway" grant shortcut stays Super-Admin-gated. |
| Employee disputes a request logged on their behalf | **Not solved by this spec** — flagged as a known open gap, not a blocker. Today's leave workflow has no "employee contests a pending request" affordance at all (this predates this feature); if wanted, it's a natural follow-up, not something to build here. |

## Testing

- `is_direct_manager_of` / `can_log_leave_for`: direct report → True; own
  manager → False; two levels down → False; inactive report → False;
  secondary-only relationship → False; no employee_profile → False.
- `LeaveRequestCreateView`: a plain manager (no special Role) can reach the
  form and see only their direct reports in the dropdown; POSTing an
  employee id outside that scope fails form validation ("Select a valid
  choice...", 200 with `form_invalid`) rather than creating anything — the
  same behavior the existing Role-scoped dropdown already has today, since
  both rely on the same `ModelChoiceField` queryset-filtering mechanism.
- A manager-logged request still creates the normal `LeaveRequestApproval`
  rows and never auto-approves.
- A manager-logged over-cap Site request is held with the same
  `exceeds_balance=True` behavior as an HR-logged one; a manager never sees
  or can trigger the "Log Anyway" grant action.
- My Profile: the "My Reporting Structure" card's Direct Reports list shows
  a "Log Leave" link for each active report (none for inactive ones), and
  it pre-fills the right employee.
