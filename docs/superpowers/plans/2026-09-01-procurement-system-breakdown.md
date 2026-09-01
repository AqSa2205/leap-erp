# Procurement — Project System Breakdown — Implementation Plan

Date: 2026-09-01
Design: `docs/superpowers/specs/2026-09-01-procurement-system-breakdown-design.md`

## Global Constraints

- **Hold the identity.** `% Delivery + Pending % == % of All` on every row.
- **Three denominators, not one.** % of system is the only one measured against
  the system; delivery and pending are measured against the project.
- **Reuse the status rule.** `budget_status.COMMITTED_STATUSES`, so this page and
  the budget percentage agree about "ordered".
- **Never invent a percentage.** No value to divide by means `None`, not `0`.
- **Visibility via `_visible_pos_for()`.** No second rule.
- Run `python manage.py test procurement` after each task.

## File Structure

```
procurement/
  system_breakdown.py        (new) grouping, percentages, delivery
  tests_system_breakdown.py  (new)
  views.py                   project_systems
  urls.py                    project/<id>/systems/
templates/procurement/
  project_systems.html       (new) lines table + system summary
  po_by_project.html         "Systems" button on each card
```

---

### Task 1: Delivered quantities — DONE

`delivered_quantities(items)` — one aggregate over `DeliveryNoteItem`, keyed by
`source_po_item`.

**Tests:** partial deliveries accumulate; a note line with no `source_po_item` is
not attributed; query count invariant with line count.

### Task 2: Rows — DONE

`build_rows(pos)` — committed orders only, converted to SAR, delivered value
capped at the line value and over-delivery flagged.

**Tests:** drafts and cancelled excluded; acknowledged and completed included;
foreign currency converted; over-delivery capped and flagged.

### Task 3: Grouping and percentages — DONE

`breakdown(pos)` — group by system, compute all four percentages, order by value
with uncategorised last.

**Tests:** grouped under their system; largest first; uncategorised kept and
sorted last; each percentage against the right denominator; the identity holds;
nothing ordered gives `None`.

### Task 4: Page — DONE

View, template, and a Systems button on each project card.

**Tests:** renders systems and shares; explains itself when only drafts exist;
403 for a viewer with no PO on the project; a viewer sees only their own orders;
the board links to it.

## Risks

- **Measuring delivery against the line.** The most natural wrong choice, and it
  destroys the column's meaning without breaking anything visible. Pinned by
  both an identity test and a direct test of the denominator.
- **Over-delivery.** Silently produces negative Pending and a % Delivery above
  % of All. Capped, flagged, tested.
- **Uncategorised lines.** Dropping them makes the shares stop totalling 100%
  with no visible symptom.
- **Disclosure.** The page names a project. A viewer with no PO against it must
  not be able to open it.

## What the build taught us

**The access test caught a real bug immediately.** `PermissionDenied` was not
imported in `procurement/views.py`, so the disclosure guard raised `NameError` —
a 500 rather than a clean 403. It still denied access, but as a server error,
and nothing else in the module had ever needed the import. Worth noting that the
guard *looked* right in review; only executing it showed otherwise.

**Real data exposed a rendering gap the tests did not.** With every line at zero
value, all percentages are correctly `None` — and the template rendered them as a
bare `%`. The tests asserted the computation, not the output, so this only
appeared when the page was rendered against the dev database. Every percentage
cell now renders an em dash instead, verified by asserting the page contains no
bare `%` cells.

**All six guards mutation-tested and all six bite:** measuring delivery against
the line fails 2, not capping over-delivery fails 1, including drafts fails 2,
dropping uncategorised lines fails 2, skipping currency conversion fails 1, and
removing the disclosure guard fails 1.
