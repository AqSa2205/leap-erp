# Procurement — Budget Consumption % — Design

Date: 2026-09-01
Module: `procurement`
Status: built

## Why

Procurement's standing question about any project is *have we spent the budget
yet, and how close are we?* Today the POs-by-project board shows how much has
been ordered and the approved-budgets page shows which budgets exist, but
nothing puts the two together. Anyone wanting the answer adds up POs by hand
against a budget they have to open separately.

A percentage on the board answers it at a glance, and — more usefully — makes
over-commitment visible before somebody raises the order that causes it.

## The hard part is not the arithmetic

Committed ÷ approved is trivial. Putting those two numbers on the same footing
is where this can be quietly wrong, and each way it can be wrong is worth
naming.

### VAT

`PurchaseOrder.total_value` is `gross_value + vat_amount`. A budgeted line price
carries no VAT anywhere in its derivation. Comparing them directly reports every
project **~15% further through its budget than it is** — consistently, in the
same direction, and invisibly, because the number always looks plausible.

Commitment is therefore measured on `gross_value`.

### Basis

`po_from_budget` seeds a PO at `budget_unit_price()`, which is written so that
"a PO's rate × qty reproduces the budgeted line total". Summing
`budget_line_price()` over the same lines is exactly the denominator those
numerators were built against, so a project procured strictly to budget lands on
**100.0%** rather than merely near it.

This is also why **optional sections are excluded**: `po_from_budget` skips them
(they are quotable extras the client has not bought), and if the two disagreed a
PO seeded from a budget would not reconcile against it.

### Currency

Budgets are SAR. POs may be SAR, USD, EUR, AED or GBP. Adding a 1,000 USD order
as 1,000 SAR understates commitment by nearly a factor of four, so orders are
converted via `ExchangeRate`.

Note the field named `rate_to_usd` actually holds **units per USD** (SAR 3.75,
USD 1.0), so X → SAR is `amount × rate_SAR / rate_X`. An unknown currency is
kept unconverted rather than dropped: losing committed spend from the figure is
worse than a rate that could not be applied. Any conversion sets a flag so the
figure can be read for what it is — current rates applied to historic orders.

### What counts as committed

| Status | Counts | Why |
|---|---|---|
| `issued` | Committed | Money is out the door |
| `client_acknowledged` | Committed | Committed and locked |
| `completed` | Committed | Committed spend does not become uncommitted by progressing |
| `draft` | **Separately** | Not a commitment — but about to be one |
| `cancelled` | Not at all | Commits nothing |

Drafts are deliberately neither counted nor hidden. Counting them overstates
what has been committed; hiding them means the person deciding whether to raise
another order cannot see the ones already queued. They get their own figure and
their own band on the bar.

## Two cases where a number would be a lie

**No approved budget is not 0%.** A project with no finance-approved sheet has an
*undefined* percentage. Rendering 0% says "nothing spent" when the truth is
"nothing to compare against" — and on a board where 0% and 100% both matter, that
is the worst possible confusion. The status carries `percent: None` and the board
prints "no budget set".

**Over budget is never capped.** Going past 100% is the single most important
thing this feature can tell anyone. Clamping the bar at 100% renders a full green
bar that reads as success. The percentage is reported as-is; only the bar's
*width* is capped, and it turns red when it does.

## What it shows

On each project card in POs by Project, beside the existing count and totals:

```
▸ Ghazlan Substation   LNA-2026-0417   4 POs   482,300.00 SAR   62.4% ▓▓▓▓▓▒▒░░
```

- The percentage, red past 100%, amber when drafts would take it past.
- A bar: solid for committed, hatched for draft, so draft reads as "not yet
  committed" rather than as more spend.
- A tooltip with the underlying SAR figures and a note when non-SAR orders were
  converted.

## Scope

- No new models, no migration. Both numbers are derived.
- No change to what POs or projects anybody can see — this reuses
  `_po_project_groups`, so visibility is unchanged.
- Budgets are fetched for the whole board in one pass; a per-project walk of
  sections and line items would issue queries in proportion to everything
  procurement tracks.
