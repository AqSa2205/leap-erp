# The name printed under each PO approval stage

## Why

A purchase order prints four signature blocks — SCM, PM, COO, CEO — each with
a person's name under the line. Those names lived in
`PurchaseOrder.APPROVAL_STAGES`, a tuple of constants in `procurement/models.py`.
A change of PM therefore needed a code edit, a review and a deploy, for a fact
about the company that nobody in procurement could state on the system that
prints it.

The Approval Routing page already existed beside it (`/procurement/approval-routing/`,
super admin only) and already mapped each stage to a **user account** — but that
only decided who gets the approval email. The page showed the printed name in a
read-only column, which is the worst of both: visible, evidently wrong after a
personnel change, and not editable.

## What changed

`POStageApprover` — the row that already carried the routing — gains
`signer_name`. The Approval Routing page edits it per stage, in the column that
previously only displayed it.

**Blank means the built-in default**, not an empty name. A signature line with
nothing under it is worse than a stale name, and it makes clearing the box the
way back rather than a way to break the document. The page shows the default as
the input's placeholder and says which state the row is in, so "why does it say
that" is answered on the page rather than in the source.

`POStageApprover.user` is now nullable. The two halves are genuinely separate
questions — who signs on paper, and which account gets the email — and a name
correction should not be blocked on answering the second. A row with a name and
no account is treated by `stage_recipients()` as unrouted, so the role-holder
fallback still fires; without that, naming a signer would have quietly stopped
telling anyone about the stage.

## The part that is not cosmetic

`is_designated_approver()` matches the signed-in user's name against the stage's
signer to decide whose **"waiting on me"** list a PO appears in. So the printed
name is also the inbox key, and a rename has to move the PO from one person's
list to another — otherwise the document would name one person while sitting in
someone else's queue. It does.

What a rename does **not** do is grant anything. `can_user_approve_stage()`
remains role-based and is the only gate on the Approve button; a test pins that
down, because "the name decides who signs" is exactly the wrong inference to
let anyone draw from this page.

Purchase orders already signed keep their recorded approver and timestamp. Only
the printed name follows the configuration.

## Reading it without an N+1

The names are read by `approval_status`, which is reached by `current_stage`,
which the approvals sidebar walks **for every open PO on every page in the app**
— the loop in `hr/approvals.py` that carries a comment about being measured at
362 queries for a badge that said 0. An unguarded lookup here would have put
that straight back.

Three layers, deliberately:

| | |
|---|---|
| `POStageApprover.effective_signer_names()` | one query, the whole map |
| `PurchaseOrder.signer_names()` | memoised per instance |
| `PurchaseOrder.prime_signer_names(pos)` | one lookup shared across a list |

All four bulk callers prime: the approvals sidebar, My Work, the flat PO list
and POs-by-project. Query-count tests assert the number of signer-name reads is
**unchanged** between two data sizes rather than pinning a number — the sidebar
reads them too, so the absolute count is not 1 and a constant would be a magic
number that any unrelated change breaks.

## Two things this turned up

**A third N+1 site.** The POs-by-project page has a query-count test of its
own, and it failed as soon as the names became a lookup - `workflow_status`
reaches them for every row. The flat PO list does the same and had **no such
test**, so it would have regressed silently; both are primed now and both are
covered by invariance tests.

**A live 500 on the PO list**, pre-existing and unrelated to this change, found
because the new test could not render the page. The list printed the creator
as `{{ po.created_by.get_full_name|default:po.created_by.username }}`, and
`created_by` is `SET_NULL`. A failed *variable* renders blank; a failed *filter
argument* raises. So deleting any user account that had ever created a purchase
order took the whole list page down with a 500 for everybody. Guarded, with a
regression test.

The same `default:` pattern on a nullable user appears in other templates.
Most are already wrapped in an `{% if %}`; the ones that are not have not been
audited here, deliberately - that is a sweep of its own, not a rider on a
rename feature.

## Verification

- **34 tests** in `procurement/tests_stage_signer.py`, covering the three states
  a half-configured system is actually in: no row, a row with a blank name, and
  a row with a name but no account.
- **16 guards mutation-tested, all killed.** Two survived the first pass:
  one was a redundant `or default` that made a broken query invisible (removed,
  so the rule is expressed once), and one was a test asserting `name="signer_name"`
  was present — which a `type="hidden"` field satisfies while leaving the name
  uneditable.
- Migration `0030_po_stage_signer_name`, single leaf.

## Not done

`is_designated_approver()` still matches on the name even though `POStageApprover`
now holds a real FK for the stage. Using the FK would be the better answer and
would make the name purely presentational — but it changes whose inbox POs land
in, which is a behaviour change beyond a rename, so it is left alone
deliberately.
