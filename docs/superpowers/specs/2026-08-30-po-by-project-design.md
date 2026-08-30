# Purchase Orders by Project — Design

Date: 2026-08-30
Module: `procurement`
Status: agreed, ready to build

## Why

The PO list is a flat, reverse-chronological table. Procurement's actual
question is usually the other way round — *what has been ordered for this
job?* — and answering it today means searching by project name and reading
across pages.

A project-first view answers it directly: every project in scope, its purchase
orders nested underneath, collapsible so the page stays navigable at the real
number of projects.

## Shape

A separate page, `/procurement/po/by-project/`, not a mode on the existing
list. The flat list is the right tool for "what came in this week" and stays
exactly as it is; mixing the two into one view with a toggle would compromise
both.

Each project renders as a collapsed card:

```
▸ Ghazlan Substation Upgrade   LNA-2026-0417   4 POs   482,300.00 SAR   1 awaiting approval
```

Collapsed by default. With a hundred-odd projects an all-expanded page cannot
be used, so the **header has to carry enough to be useful closed** — count,
total value and how many are waiting on somebody. Otherwise the reader expands
every card looking for the one that matters, which is worse than the flat list
they started with.

## Which projects appear

Not every project — only the ones that are procurement's business. Three doors
onto the board:

1. **Finance approved its budget.** The same rule the Approved Budgets page
   uses, so the two pages agree about what is in front of procurement.
2. **Procurement selected it.** For work that needs procuring before a budget
   exists, or that never goes through one. Recorded as a `ProcurementProject`
   with who added it and why.
3. **Orders already exist against it** — shown, but **flagged**.

A project still being bid is not procurement's work, and listing the whole
pipeline buries the jobs that are.

The third door needs explaining. A PO against a project with no approved
budget and no selection is an anomaly, but hiding it would lose committed
spend from the page entirely. So it appears with a "Not on the board" marker
and a banner counting them — which is also how the gap gets closed, since
somebody can then add it properly or chase the budget. Selecting it clears the
flag.

An approved budget with nothing ordered yet still appears: that is a real
answer to "what has been procured for this job", and the more interesting one
— it is work not started.

**Removal only undoes a selection.** Taking a project off the board cannot
un-approve a budget or un-raise a purchase order, so a project that qualifies
by either of the other two doors stays.

Who maintains it: the procurement team plus the admin tiers. It is their own
working list, matching who can already reach Approved Budgets.

Scope follows `_visible_pos_for()` rather than inventing a second rule:

| Viewer | Projects listed |
|---|---|
| Super admin, procurement | Every approved budget, plus anything with POs |
| Admin, manager | The same, limited to their region |
| Anyone else | Only projects they have raised a PO against |

That last row matters. A user who can only see their own POs must not be shown
a list of every project name in the business as a side effect — so for them
the page is built purely from the projects their own POs belong to, and
zero-PO projects are omitted entirely.

## Purchase orders with no project

`PurchaseOrder.project` is nullable and `project_name` is free text, so a PO
can exist with no project link at all. Those go into an **Unassigned** group
at the end, showing whatever `project_name` says.

They are not dropped and not silently folded into a project by matching the
free-text name — a fuzzy name match would quietly attribute spend to the wrong
job. Grouping them visibly is also the only way anybody notices the link is
missing and fixes it.

## Totals

`PurchaseOrder.total_value` is a property that walks `items`, so a naive
grouping would issue one query per PO. The view prefetches items and computes
per-project totals in one pass.

Currency is **not** summed across differing currencies. Where a project's POs
are all in one currency the total is shown with it; where they are mixed, the
header shows the count and a per-currency breakdown rather than a single
meaningless number.

## What each row shows

Inside an expanded project, one row per PO: number, vendor, date, status,
approval stage (the same `workflow_status` the flat list shows), value, and a
link to the PO. The approval stage is the reason to open the card at all —
it answers "is anything for this job stuck".

## What this does not do

- No change to the flat PO list.
- No change to permissions. The same POs are visible, arranged differently.
- No editing from this page; it links out to the PO.
