# Purchase Order Workflow, Locking and Import — Design

Date: 2026-08-30
Module: `procurement`
Status: agreed, ready to build

## Why

Five gaps reported together, all on the purchase order:

1. A PO in the approval chain does not say **which stage it is waiting on**. The
   data is there — `PurchaseOrder.current_stage` — but no list or detail view
   surfaces it, so finding out means opening the PO and reading the timeline.
2. There is no status meaning **the client has acknowledged the PO**, and
   therefore nothing that freezes a PO once it has been accepted.
3. Excel import exists only as a **one-shot create**. A PO cannot take more
   lines from a spreadsheet after it exists.
4. An approver is **not told** a PO is waiting on them.
5. Procurement's Approved Budgets page does not show the **LNA reference**, so
   a budget cannot be tied back to the proposal it came from without opening it.

## Decisions taken

Three questions changed the design and were settled before building.

### 1. `acknowledged` is renamed, not duplicated

`PurchaseOrder.STATUS_CHOICES` already contains `acknowledged`, but it carries
no behaviour — it only picks a badge colour in `po_list.html`,
`po_detail.html` and the two summary templates. Rather than add a second,
near-identical status and leave two "acknowledged" entries in one dropdown, the
existing value is **renamed in place** to `client_acknowledged` and given the
locking behaviour.

A data migration moves existing rows. This is safe because the old status
never gated anything: no view, form or query branches on it beyond display.

### 2. The lock is absolute in normal use, with an audited super-admin release

Once a PO is Client Acknowledged:

- the PO form rejects any change to any field,
- terms and conditions cannot be toggled, overridden or edited,
- line items cannot be added, edited, deleted or imported,
- approval stages cannot be signed.

A **super admin** may move it back out of that status. That is the only route,
it requires a reason, and it is recorded. This matches how override works
elsewhere in the app (attendance exceptions, leave) — the alternative, a
permanent freeze, means a typo can only be resolved by cancelling the PO and
raising a new one.

The lock is enforced in `PurchaseOrder.is_locked` and checked in **every**
write path, not in the template. A template-only lock hides the buttons and
leaves the endpoints open.

### 3. Stage approvers become real users

`APPROVAL_STAGES` names its signers as plain strings — `('pm', 'PM Approval',
'Ali Sultan')`. There is no link from a stage to a user account, so there is no
address to email. `can_user_approve_stage()` works on roles, which is fine for
a permission gate and useless for a recipient.

A new `POStageApprover` model maps a stage key to a user, editable by admins.
Where a stage has no mapping, the notification falls back to every user holding
that stage's role, so the feature degrades to "someone is told" rather than
"nobody is told".

This also repairs the same weakness in the Pending Approvals inbox, which
currently matches the signer by name.

## Model changes

```
PurchaseOrder
  + client_acknowledged_at    DateTimeField(null)
  + client_acknowledged_by    FK(User, null)
  ~ status                    'acknowledged' -> 'client_acknowledged'
  + is_locked                 property  (status == 'client_acknowledged')

POStatusChange           (new)   status history + the audit trail for unlocking
  purchase_order  FK(PurchaseOrder, related_name='status_changes')
  from_status     CharField
  to_status       CharField
  reason          TextField(blank)
  changed_by      FK(User, null)
  changed_at      DateTimeField(auto_now_add)

POStageApprover          (new)   who signs each stage
  stage           CharField(unique, choices from APPROVAL_STAGES)
  user            FK(User)
  updated_by      FK(User, null)
  updated_at      DateTimeField(auto_now)
```

`POStatusChange` is deliberately a log of **all** status transitions, not only
unlocks. The unlock audit was the requirement; a status history is the same
table and answers "when did this PO become live" for free.

## Workflow status

Derived, never stored. A stored copy of `current_stage` is a second source of
truth that goes stale the moment someone signs.

`PurchaseOrder.workflow_status` returns a small dict:

| PO state | label | tone |
|---|---|---|
| Draft, unsigned | `Draft` | neutral |
| Awaiting a stage | `Awaiting PM approval` | warning |
| All stages signed | `Approved — released` | success |
| Client acknowledged | `Client acknowledged — locked` | locked |
| Cancelled | `Cancelled` | muted |

Shown as a column on the PO list and a banner on PO detail. The label names the
stage that is actually next, taken from `current_stage['label']`, so it cannot
drift from what the Approve button will accept.

## Excel import

Two entry points, one parser.

- **On create** — the existing `po_import_excel` continues to build a PO from
  the template, and now accepts **several files in one submission**, applied in
  order.
- **After create** — a new `po_import_items` appends line items to an existing
  PO from a spreadsheet.

The parser follows `ProjectImportView`'s approach rather than the current
fixed-cell reading: find the header row, map columns by name, tolerate column
order and extra columns. Fixed cell offsets break the moment someone inserts a
column, and the sales import already solved this.

Import is **append-only** and refuses a locked PO. Every import reports what it
read — rows added, rows skipped and why — rather than silently importing a
subset.

## Approver notification

When a PO enters a stage that is waiting on someone, that person is emailed and
notified in-app. "Enters a stage" means either the PO reaching its first stage,
or a stage being signed so the next one becomes current.

The email states the PO number, vendor, value and the stage, and links straight
to the PO detail page where the signature panel lives. Sent through the existing
`notifications.services.notify_users` path so it inherits the Graph email
backend and the in-app bell, and sent **after** the approval transaction
commits so an email never describes a signature that rolled back.

## LNA references on Approved Budgets

`project.proposal_reference` is the LNA reference. The Approved Budgets table
gains a **Reference** column linking to the project, and the reference is added
to the page's search so procurement can find a budget by the reference the rest
of the business uses.

## What this does not do

- No change to who may approve a stage. `can_user_approve_stage()` is
  untouched; `POStageApprover` decides who is *told*, not who is *allowed*.
- No change to the approval sequence or the CEO threshold.
- Import does not update or delete existing lines. Append only.
