# Purchase Order Workflow, Locking and Import — Implementation Plan

Date: 2026-08-30
Design: `docs/superpowers/specs/2026-08-30-procurement-po-workflow-design.md`

## Global Constraints

- **Enforce locking in the write path, never in the template.** Hiding a button
  does not close an endpoint. Every mutating view and form that touches a PO,
  its items, its terms or its signatures must check `is_locked` itself.
- **Workflow status stays derived.** Read `current_stage`; never store a copy.
- **Notify after commit.** An approval email must not describe a signature that
  then rolls back.
- **Import is append-only** and reports what it did — rows added, rows skipped,
  and the reason for each skip.
- **`can_user_approve_stage()` is not touched.** Permission and notification are
  separate concerns; `POStageApprover` decides who is told.
- Run `python manage.py test procurement` after each task; run the full suite
  before the PR.

## File Structure

```
procurement/
  models.py              POStatusChange, POStageApprover, status rename,
                         is_locked, workflow_status
  migrations/            00XX_po_client_acknowledged.py (schema + data)
  forms.py               lock guard on PurchaseOrderForm, POStageApproverForm
  views.py               lock guards, po_import_items, multi-file import,
                         unlock view, approver admin, budgets reference
  notifications.py       (new) approver resolution + notify_stage_approver
  excel_import.py        (new) shared header-mapped parser
  urls.py                new routes
  tests.py               tests per task
templates/procurement/
  po_list.html           workflow status column
  po_detail.html         status banner, lock notice, import-items panel
  po_form.html           locked notice
  approved_budgets.html  Reference column
  stage_approvers.html   (new) admin screen
```

---

### Task 1: Status rename + locking fields on `PurchaseOrder` — DONE

- Rename `('acknowledged', 'Acknowledged')` to
  `('client_acknowledged', 'Client Acknowledged')` in `STATUS_CHOICES`.
- Add `client_acknowledged_at`, `client_acknowledged_by`.
- Add `is_locked` property — `status == 'client_acknowledged'`.
- Data migration mapping existing `acknowledged` rows to the new value, with a
  reverse that maps back so the migration is not one-way.
- Update the four templates that branch on the old value
  (`po_list`, `po_detail`, `summary_internal`, `summary_external`) and the
  status loop in `views.py:253`.

**Tests:** existing rows migrate; `is_locked` is true only for the new status.

### Task 2: `POStatusChange` log — DONE

- Model per the design.
- Record a row on every status transition, from wherever status changes
  (`PurchaseOrderForm`, the unlock view, any programmatic setter).
- Set `client_acknowledged_at/by` when entering the locked status.

**Tests:** a status change writes exactly one row with the correct from/to; the
acknowledgement stamps are set.

### Task 3: Lock enforcement — DONE

Guard every write path. At minimum:

- `PurchaseOrderForm.clean` — reject any edit to a locked PO.
- `POUpdateView`, `PODeleteView`.
- `ajax_po_item_field_update`, `ajax_po_toggle_term`, `ajax_summary_entry_update`.
- `po_approve_stage`, `po_edit_signature`.
- Item add/edit/delete routes, and both import views.

Each returns a clear refusal naming the reason, not a generic 403.

**Tests:** one per endpoint, asserting a locked PO is refused **and the data is
unchanged** — a test that only checks the status code passes against a view
that refuses after writing.

### Task 4: Super-admin unlock — DONE

- View accepting a reason, super admin only, writing a `POStatusChange`.
- Control on PO detail, visible only to a super admin, only when locked.

**Tests:** super admin can unlock with a reason; the reason is required; an
admin and a procurement user cannot; the unlock is logged.

### Task 5: Workflow status — DONE

- `workflow_status` property returning `{label, tone, stage_key}`.
- Column on the PO list, banner on PO detail.
- Filter on the list by workflow state, so "what is waiting on PM" is one click.

**Tests:** each PO state produces the right label; a signed stage moves the
label to the next stage; a released PO reads as released.

### Task 6: Shared Excel parser — DONE, scope narrowed

- `excel_import.py` with header-row detection and column mapping, following
  `ProjectImportView`'s approach.
- Returns parsed rows plus a per-row skip reason.

**Tests:** columns in any order; extra columns ignored; missing required column
reported by name; a junk file fails with a readable message rather than a
traceback.

**Deviation from the plan, decided while building.** The plan said to move the
existing template importer onto header mapping too. It was left on fixed cell
positions, and the new parser is used only for the item import.

The reason is that the two importers read different kinds of file. The
template importer reads **our own export**, whose cell positions are fixed
because we write them, and it is already covered by tests. Header mapping
solves a problem it does not have, and rewriting 250 lines of working parsing
to gain nothing is how a regression gets introduced. The new parser reads
**someone else's spreadsheet** — a vendor quotation, a client BOQ — where
column order genuinely cannot be relied on. That is where the mapping earns
its place.

The plan's own risk note ("keep the old template working and covered by a
test") pointed at this; narrowing the scope is the cheaper way to honour it.

### Task 7: Multi-file import on create — DONE

- `po_import_excel` accepts several files, applied in order, reporting per file.

**Tests:** two files produce the combined result; one bad file does not lose the
good one's rows.

### Task 8: Import items into an existing PO — DONE

- `po_import_items` appending line items; refuses a locked PO.
- Panel on PO detail with a preview of what will be added before committing.

**Tests:** items append rather than replace; totals recalculate; locked PO
refused; a partial file reports which rows were skipped.

### Task 9: `POStageApprover` + admin screen — DONE

- Model, form, list/edit screen for admins.
- Resolution helper: mapped user, else every user holding the stage's role.

**Tests:** the mapping resolves; the role fallback fires when unmapped; an
unmapped stage with no role holder resolves to nobody rather than erroring.

### Task 10: Approver notification — DONE

- `notify_stage_approver(po)` — in-app notification plus email with a deep link
  to the PO.
- Fired when a PO first enters an approval stage and after each signature moves
  the chain on, **after** the transaction commits.
- Not fired for a released, cancelled or locked PO.

**Tests:** the right person is notified on entering a stage; signing notifies
the next approver, not the previous; a released PO notifies nobody; nothing is
sent if the approval fails.

### Task 11: LNA reference on Approved Budgets — DONE

- Reference column linking to the project; add it to the page search.

**Tests:** the reference is rendered; search by reference finds the budget.

### Task 12: Full verification — DONE

- Full suite, mutation-check the lock guards and the notification target,
  update this plan with anything learned, and list both documents in the PR.

## What the build taught us

Four things worth carrying into the next piece of procurement work.

**A real bug the tests caught.** The Excel column aliases were written the way
a header reads — `rate/unit`, `make/model` — but compared against normalised
text where the slash has already become a space. `Rate/Unit (SAR)` silently
failed to match its own alias, so every imported rate was zero and a PO would
have looked complete and been worth nothing. Normalise both sides of a
comparison, not one.

**Three tests passed against broken code before being fixed.** Each was the
same shape: the assertion was satisfied by something other than the behaviour
under test.

- An item-edit test used a field outside the inline-editable allowlist, so it
  got a 400 whether or not the lock existed.
- The signature test posted an empty signature, which the validator rejects
  independently of the lock.
- The form-nesting test compared against the first `</form>` on the page,
  which belongs to the navigation bar.

The lesson is mechanical: after writing a guard, remove it and check the test
actually fails. Two of these were only found that way.

**Mutations interact.** Removing three notification rules at once masked one
of them — the role fallback filtered out the inactive user that the broken
mapping had let through. Mutate one rule at a time when a result looks too
good.

**`transaction.on_commit` does not fire under `TestCase`.** It wraps each test
in a transaction it rolls back, so a queued notification never runs and a test
asserting on it passes vacuously. `self.captureOnCommitCallbacks(execute=True)`
is the fix, and the deferral is deliberate — an email describing a signature
that rolled back cannot be recalled.

## Risks

- **The lock has many doors.** Terms, items, signatures, summary editing and
  both imports are separate endpoints. Missing one leaves the PO editable while
  the UI says it is frozen. Task 3 enumerates them; grep for every route that
  writes to a PO before calling it done.
- **The status rename touches display code in four templates and one view
  loop.** A missed branch shows a blank badge rather than erroring, which is
  easy to ship unnoticed.
- **Existing PO imports rely on fixed cell positions.** Moving to header
  mapping is the right fix but changes behaviour for files that happen to
  parse today; keep the old template working and covered by a test.
