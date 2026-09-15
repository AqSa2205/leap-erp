# Manpower Costing module — Phase 1

**Shipped:** 13 September 2026 · **App:** `manpowercost` · **URL:** `/manpower-costing/`

## Why

Two workbooks were the source of truth for manpower pricing, and neither was
safe to price from.

`Updated_Manpower Costing Sheet (2).xlsx` — the `Manpower Costing Sheet` tab had
its **Employee Name and Gross Salary columns emptied** while every formula was
left live. `X7 =SUM(K7:W7)*12` does not error when `K` is blank; it silently
drops salary out of the total. The same person, same formula, came to **14,376
SAR/yr on that tab and 232,251 on the file's own `Sheet1`**. Company totals:
**195,270 vs 5,188,569**. The tab showed 20 staff where `Sheet1` had 34, and its
Costing Summary — including a blended **67.80 SAR/hr** — was computed from those
salary-free numbers.

`Copy of MANHOUR (Autosaved).xlsx` — in `REAL COST`, three labels contradicted
their formulas: **"Overhead 10%" computed `R*5%`**, **"Profit 25%" computed
`R*20%`**, **"Monthly Inv 10.5 months" divided by 11**. The Taiba tabs link
their charge rates to this sheet (`E3 ='REAL COST'!W12`), so every Taiba price
inherited all three. The two files also disagreed on the hour basis — **240
h/month (30 × 8) in one, 176 in the other** — which alone moves an hourly rate
by ~36%. Jubail's `1.13` sits in `C14` and is not applied to the total.

The ERP's existing `manpower` app carried the same class of defect:
`total_yearly_cost` summed its components **with no ×12** while `sheet_import`
read the workbook's *monthly* columns straight into them, so every imported
sheet reported a yearly cost that was really monthly. It had **no tests**, was
hardcoded to `is_super_admin_user or is_admin_user` with no entry in
`accounts/permissions.py`, and its importer created the sheet before parsing,
with no transaction and no preview.

## What this module does

Cost is entered **once, per month**, and everything else is derived. The
charge-out rate is built from that cost through **named parameters** rather than
labels, and every step of the build-up is shown.

### Models (`manpowercost/models.py`)

- **`CostBasis`** — `overhead_pct`, `profit_pct`, `billable_months`,
  `hours_per_month`, `working_days_per_month`. Marking one default demotes the
  others, so `get_default()` can never depend on insertion order.
- **`ManpowerCostSheet` / `ManpowerCostLine`** — 15 cost components, all
  explicitly monthly in their field labels. Optional FK to `hr.Employee`
  alongside a snapshotted `employee_name`, and an optional FK to
  `costing.ResourceCatalogueItem` for the role.
- **`ChargeRate`** — one row per (position, classification, basis).
  `manual_rate` overrides the derived value; margin is computed, never stored.

### Logic (`manpowercost/rates.py`)

Pure functions, no ORM writes — same shape as `pmo/progress.py`.
`derive_charge_rate()` returns **every intermediate step**, not just the answer,
because a single opaque number is what made the source sheets uncheckable.
Overhead and profit are both taken on the base cost and never compounded, which
is what `REAL COST` actually did.

### Entry and use

Cost lines are typed on screen, one row per person, saved on blur - the same
grid pattern as costing's A.4 resources, because this is the "type twenty
lines in one sitting" job that pattern exists for. A cell that cannot be
parsed is **refused**, and the previous value stands; the Excel importer this
replaced turned anything it could not read into a silent zero, which is how
both source workbooks came to under-report.

### In the costing sheet

A.4 Resources now shows a **Std rate** column: the charge-out rate this module
derives for that role, offered beside the rate actually typed. Clicking it
applies it through the grid's own save path. Where the viewer holds
`manpowercost.margin`, a **Margin** column shows what the sheet earns against
that role's cost - computed from the rate on the line, not the standard one,
because what this sheet charges is the question.

Nothing is applied automatically: a negotiated rate stays as typed.

### Access

Three separate gates, where the old app had one role check doing all three jobs:

| Gate | What it controls |
|---|---|
| `manpowercost.access` / `.nav` | opening the pages |
| `_user_can_see_pricing` (shared with costing) | whether money is in the response **at all** |
| `manpowercost.margin` | whether the markup is shown |

Seeded defaults are super_admin and admin — exactly who could reach the old
app — so nobody gains or loses access on deploy.

## Carry-over from `manpower`

`0002_seed_basis_and_migrate_manpower` copies the old sheets across, **reading
the figures as monthly**, which is what the old importer actually wrote. That
assumption cannot be checked per row, so it is written onto each migrated
sheet's notes. The migration is idempotent.

**The old tables are deliberately not dropped.** They are the only copy of that
data, and a migration that both transforms and destroys leaves nothing to
compare against. Dropping them is a separate follow-up, once the migrated data
has been eyeballed.

## Verification

- **81 tests** in the app. Single migration leaf per app, checked through the
  loader graph.
- The anchor test reproduces **REAL COST row 12** (Hydraulic Engineer →
  **219.7443 SAR/hr**) from 5% / 20% / 11 months / 176 h, proving the module
  reproduces what was actually charged rather than what the labels claimed.
- **Nine guards mutation-tested, all killed.** Two survived the first pass and
  found real gaps: nothing asserted that a manual rate is actually *used* (only
  that it is stored), and nothing asserted that a reader is not shown edit
  controls that would 403. Both now covered.
- Query-count **invariance** across two data sizes on both list and detail.

## Not in Phase 1

**Project pricing sheets** — the MANHOUR side: positions × quantity ×
time-phased monthly hour allocation, priced off `ChargeRate`, totalled per
project with margin against the cost register. Also deferred: the `ajeer`
contract type, which is a materially different cost model, and creating charge
rates from a cost sheet in bulk (they are entered in the admin today).
