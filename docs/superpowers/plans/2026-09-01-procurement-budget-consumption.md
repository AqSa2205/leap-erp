# Procurement — Budget Consumption % — Implementation Plan

Date: 2026-09-01
Design: `docs/superpowers/specs/2026-09-01-procurement-budget-consumption-design.md`

## Global Constraints

- **Ex-VAT on both sides.** Use `gross_value`, never `total_value`.
- **Same basis as `po_from_budget`.** Non-optional sections only, summed via
  `budget_line_price()`.
- **Never invent a percentage.** No budget means `None`, not `0`.
- **Never cap the percentage.** Only the bar width is capped.
- **One pass for the whole board.** No per-project budget query.
- Run `python manage.py test procurement` after each task.

## File Structure

```
procurement/
  budget_status.py         (new) the reconciliation
  tests_budget_status.py   (new)
  views.py                 _build_group / _po_project_groups wiring
templates/procurement/
  po_by_project.html       percentage + bar on each card
```

---

### Task 1: The denominator — DONE

`approved_budgets_for(projects)` → `{project_id: Decimal}` in SAR, over
`finance_approved` sheets, non-optional sections only, prefetched.

**Tests:** sums line prices; ignores unapproved sheets; ignores optional
sections; sums several approved sheets on one project; absent rather than zero
when there is no budget; query count invariant with project count.

### Task 2: The numerator — DONE

`commitment(pos)` → committed / draft / converted, in SAR, ex-VAT, cancelled
excluded.

**Tests:** VAT excluded; cancelled commits nothing; draft reported separately;
acknowledged and completed still count; foreign currency converted; conversion
flagged; unknown currency kept.

### Task 3: The status — DONE

`budget_status(budget, pos)` → percentage, remaining, `over_budget`,
`draft_would_exceed`.

**Tests:** percentage is committed ÷ budget; no budget → `None`; zero budget →
`None`; over budget reported not capped; drafts that would break the budget
flagged before issue; remaining ignores draft.

### Task 4: The board — DONE

Wire into `_build_group`; percentage, bar and tooltip on each card.

**Tests:** group carries the percentage; VAT ignored end to end; page renders
the figure; page says "no budget set" when there is none; query count invariant.

## Risks

- **VAT is the silent one.** It is a consistent 15% overstatement that always
  looks plausible, so it would survive review and eyeballing. Pinned by a test
  asserting a 1,000 SAR order against a 1,000 SAR budget is 100%, not 115%.
- **The zero/None distinction.** Easy to "simplify" into `or 0` later, which
  turns "no budget" into "nothing spent". Two tests guard it.
- **Currency.** Adding raw would understate by ~3.75×. Guarded, and any
  conversion is flagged rather than presented as exact.
- **Query growth.** The budget walk touches sections and line items. Both query
  tests compare two data sizes rather than pinning a number.

## What the build taught us

**A pinned query count failed immediately — mine.** The first version of the
budget lookup test used `assertNumQueries(3)` and the real answer was different.
That is the exact anti-pattern flagged in a recent PR review, reproduced here
within an hour of writing the review. Both query tests now compare the count at
two data sizes and assert equality, which is the version that cannot be
"fixed" by updating a number.

**All six guards mutation-tested and all six bite:** comparing on `total_value`
fails 7 tests and errors 7 more, counting cancelled orders fails 1, counting
drafts as committed fails 3, skipping currency conversion fails 1, treating no
budget as 0% fails 2, and including optional sections fails 1.
