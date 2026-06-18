# Team Activity Tracking — Design Spec

**Date:** 2026-06-18
**Module:** `kpis` (extends the existing Department KPIs app)
**Status:** Approved design — ready for implementation plan

## Problem

The KPI module measures **outcomes** (win rate, on-time delivery, etc.). Management
also wants a view of **activity / throughput** — how active each person is on the
ERP: e.g. "Person A created 34 pipelines, created BOMs, finalised sales, transferred
to finance." This is a per-user **performance review across all modules**, viewable
for every ERP user, that can later be attached to the KPIs.

This is distinct from "KPIs by Person" (which slices outcome KPIs by owner/creator).
Activity tracking counts *actions taken*, not deal outcomes.

## Decisions (from brainstorming)

- **Scope:** all modules (Pipeline, Costing, Procurement, Proposals, Dev Tracking).
- **Layout:** overview table of all users + drill-in per-user detail.
- **Time:** period selector (month / quarter / year) + all-time, reusing the KPI period control.
- **Access:** super_admin only (independently toggleable later).
- **Summary:** raw counts per activity + a simple "total actions" number (no weighting yet).
- **Approach:** live aggregation via an activity registry — no new tables, computed on
  read from existing `created_by` / handover actor fields. Works on all historical
  data with no backfill.

## Architecture

Lives inside the `kpis` app (super-admin-only, cohesive with KPIs, no new Django app).

New files:
- `kpis/activity.py` — the `ACTIVITY_METRICS` registry + bulk count functions.
- `kpis/activity_service.py` — `build_activity_overview(period)` and
  `build_user_activity(period, user)`.
- Views added to `kpis/views.py`; routes to `kpis/urls.py`.
- Templates `kpis/templates/kpis/activity_overview.html`, `activity_detail.html`.
- One accounts data migration seeding the new capability.

Reuses: `kpis/periods.py` (period parsing + selector options), the
`@require_capability` gate, and the existing "Department KPIs" nav section.

### Access control

New enforced capability **`kpis.activity`** (super_admin only by default), separate
from `kpis.access` so activity can later be widened to managers without exposing the
KPI numbers. Added to `CAPABILITIES`, granted to `super_admin` in
`DEFAULT_CODENAME_GRANTS`, seeded by a new accounts migration
(`00XX_seed_kpis_activity_permission`) calling `seed_default_permissions()`.

## Activity registry

`ACTIVITY_METRICS` is a list of `ActivityMetric` records, each declaring:

- `key` — stable slug
- `module` — display group (Pipeline / Costing / Procurement / Proposals / Dev Tracking)
- `label` — verb phrase ("Pipelines created", "Sales finalised", …)
- `headline` — bool; whether it is a column in the overview table
- `counts(start, end) -> {user_id: int}` — one grouped query
  (`.filter(<date> in window).values(<actor>).annotate(n=Count('id'))`),
  no date filter when `period` is all-time.

Each metric maps to a model + actor FK + date field:

| key | model | actor field | date field | headline |
|---|---|---|---|---|
| `projects_created` | projects.Project | created_by | created_at | ✅ Pipelines |
| `status_changes` | projects.ProjectHistory | changed_by | changed_at | |
| `documents_uploaded` | projects.Document | uploaded_by | uploaded_at | |
| `project_revisions` | projects.ProjectRevision | created_by | created_at | |
| `boms_created` | costing.CostingSheet | created_by | created_at | ✅ BOMs |
| `boms_handed_to_sales` | costing.CostingSheet | handed_over_by | handed_over_at | |
| `costing_started` | costing.CostingSheet | costing_started_by | costing_started_at | |
| `sales_finalised` | costing.CostingSheet | finalized_by | finalized_at | ✅ Sales finalised |
| `handed_to_finance` | costing.CostingSheet | finance_review_by | finance_review_at | ✅ To finance |
| `finance_approved` | costing.CostingSheet | finance_approved_by | finance_approved_at | |
| `pos_created` | procurement.PurchaseOrder | created_by | created_at | ✅ POs |
| `po_scm_approvals` | procurement.PurchaseOrder | scm_approved_by | scm_approved_at | |
| `po_pm_approvals` | procurement.PurchaseOrder | pm_approved_by | pm_approved_at | |
| `po_coo_approvals` | procurement.PurchaseOrder | coo_approved_by | coo_approved_at | |
| `po_ceo_approvals` | procurement.PurchaseOrder | ceo_approved_by | ceo_approved_at | |
| `delivery_notes` | procurement.DeliveryNote | created_by | created_at | |
| `inventory_reports` | procurement.InventoryReport | created_by | created_at | |
| `tech_proposals` | proposals.TechnicalProposal | created_by | created_at | ✅ Proposals |
| `pqds` | proposals.PrequalificationDocument | created_by | created_at | |
| `stacks_created` | devtracking.TaskStack | created_by | created_at | |
| `tasks_completed` | devtracking.DevTask | developer | completed_at | ✅ Tasks done |

(Note: `DevTask` has no creator/created-date field, so "tasks created" is not
attributable; `TaskStack.created_by` is used for dev-side creation activity, and
completed tasks attribute to the `developer` who finished them.)

Headline columns (overview table): Pipelines, BOMs, Sales finalised, To finance, POs,
Proposals, Tasks done, plus **Total** (= sum of **all** metrics, not just headline).

A bad metric (e.g. a model import issue) degrades to zero counts rather than 500-ing
the page, mirroring the KPI module's defensive computation.

## Data flow

1. View resolves the period (reusing `_resolve_period`), default current quarter; an
   "all-time" option is added to the period dropdown.
2. `build_activity_overview(period)`:
   - Resolves the date window (None for all-time).
   - Runs each metric's `counts(start, end)` **once** → ~21 grouped queries total
     (not per-user).
   - For every active user, assembles `{metric_key: count}`, computes `total` =
     sum across all metrics, and a headline subset for the table columns.
   - Returns rows for **all active users** (including zero-activity), each with
     `{user, role, headline_counts, total}`.
3. `build_user_activity(period, user)` reuses the per-metric dicts (or re-queries
   filtered to the user) and groups all metrics by module for the detail page.

## Presentation

- **Overview** (`/kpis/activity/`): sortable table — rows = active users
  (name + role), columns = the 7 headline metrics + Total. Default sort by Total desc.
  Client-side column sort (small dataset). Period selector top-right. Clicking a row
  opens that user's detail.
- **Detail** (`/kpis/activity/<user_id>/`): user header, then a card per module with
  every metric and its count, plus the overall total. Period selector. A clearly
  labelled "Performance score (coming soon — to attach to KPIs)" placeholder slot,
  with no computed value yet.
- **Nav:** a "Team Activity" link in the existing "Department KPIs" sidebar section,
  gated by `kpis.activity`.

## Testing

- **Registry counts:** create records owned by different users across modules; assert
  per-user counts and that period scoping (in-window vs out-of-window dates) is honoured;
  all-time ignores dates.
- **Overview service:** all active users appear (including zero-activity); totals equal
  the sum of all metric counts; headline subset correct.
- **Detail service:** per-user breakdown grouped by module; counts match.
- **Permissions:** `kpis.activity` seeded ON for super_admin only; super_admin gets 200
  on overview + detail, admin/manager get 403.

## Migrations

- One accounts data migration seeding `kpis.activity` (super_admin only). No model
  changes → no `kpis` schema migration.

## Out of scope (future)

- A weighted **performance score** combining activities (the placeholder slot is where
  it will live) and wiring it into the KPI scorecards.
- Widening access beyond super_admin.
- Caching the aggregate queries (only needed if the user count grows large).
