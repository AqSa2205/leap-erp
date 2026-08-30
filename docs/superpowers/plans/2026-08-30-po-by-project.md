# Purchase Orders by Project — Implementation Plan

Date: 2026-08-30
Design: `docs/superpowers/specs/2026-08-30-po-by-project-design.md`

## Global Constraints

- **Reuse `_visible_pos_for()`.** A second visibility rule is a second thing to
  get wrong; this page must show exactly the POs the flat list would.
- **Never leak project names.** A viewer who can only see their own POs sees
  only the projects those POs belong to — no zero-PO projects.
- **One pass, no N+1.** `total_value` walks `items`, so prefetch before
  grouping.
- **Never sum across currencies.** A single number over mixed currencies is
  worse than no number.
- Run `python manage.py test procurement` after each task.

## File Structure

```
procurement/
  views.py            po_by_project
  urls.py             po/by-project/ route
  tests.py            grouping, scoping, totals
templates/procurement/
  po_by_project.html  (new) collapsible project cards
templates/base.html   nav entry
```

---

### Task 1: Grouping helper — DONE

- `_group_pos_by_project(user)` returning ordered groups:
  `{project, po_count, rows, totals_by_currency, awaiting_count}`.
- Prefetch `items`; select_related `project`.
- Unassigned group last.

**Tests:** POs land under their project; a PO with no project lands in
Unassigned; the query count does not grow with the number of POs.

### Task 2: Scope — DONE

- Projects come from the same rule as `_visible_pos_for()`.
- Zero-PO projects included **only** where finance has approved a budget,
  and **only** for viewers with region-or-wider reach.

**Tests:** each tier sees the right project set; a restricted viewer sees no
project they have no PO against.

### Task 3: Totals — DONE

- Per-currency subtotals; count of POs awaiting an approval stage.

**Tests:** mixed currencies produce a breakdown rather than one number; the
awaiting count matches `workflow_status`.

### Task 4: Template — DONE

- Collapsible cards, collapsed by default, header carrying count, value and
  awaiting count.
- Inside: PO number, vendor, date, status, approval stage, value, link.
- Toggle to hide zero-PO projects.

**Tests:** the page renders; a project header shows its summary while
collapsed; a zero-PO project is visible and marked.

### Task 5: Navigation — DONE

- Sidebar entry under Purchase Orders.

**Tests:** the link is present for someone who can reach the page, and absent
for someone who cannot.

## Risks

- **Zero-PO projects are a disclosure decision, not a display one.** Getting
  Task 2 wrong shows a restricted user every project name in the business.
  Test the restricted tier explicitly rather than assuming the queryset filters
  it.
- **The collapsed header is the whole design.** If it does not say enough, the
  page is slower to use than the flat list it is meant to improve.
- **Query count.** Easy to regress later; assert it with `assertNumQueries`
  rather than trusting a prefetch to stay in place.

## What the build taught us

**The disclosure test could not fail.** The restricted viewer in the fixture
had no region, so the region filter emptied the project list on its own and the
guard the test existed to check was never exercised — removing the guard left
the test green. Giving that user a region made it bite. When a test protects a
guard, make sure the fixture actually reaches it.

**Query counts: assert invariance, not a number.** The first version pinned
`assertNumQueries(4)` and the real answer was 3. A fixed number breaks on any
unrelated query change and, worse, invites being updated to whatever the code
happens to do — which is how an N+1 gets accepted rather than fixed. It now
compares the count at two data sizes and asserts they are equal.
