# Project Management — milestones and the delivery board

Phase 1 of moving project delivery off `All Projects Overview 30-July-2026.xlsx`.

## Why

The workbook is 21 sheets with a four-layer chain of hardcoded cross-references, and its
reporting is already wrong in ways nobody can see from inside it:

- `Dashborad!G11` points at `Budget!C1` instead of `C15`, so the ITC project reports SAR 0 cash-in.
- `Projects Overview!P1/P2` sum only rows 7–13 while the data runs to row 17 — total cash-in
  reads 47.0M against an actual 54.9M.
- `P02195!I14` multiplies by an empty cell, zeroing a whole Value column.
- `TODAY()` is used as a stored field in ~40 places, so the file can never say when a number
  was actually last touched.
- Eight project names resolve through an external SharePoint file.

None of these are typos to fix. They are what a spreadsheet of that shape does over time.

## What Phase 1 covers

The per-project milestone WBS and the board derived from it. That is where the recurring work
is: the **completed fraction per activity** is the only thing that changes weekly, about 60
cells across all projects. Everything else on the old Projects Overview and Budget sheets is
either derived or already in the ERP.

Out of scope, deliberately: Issue Log, Faults & Losses (deviations), Man Power Activity
(project assignment), Man Power Status. The last needs Iqama/national-ID access-controlled
separately from the project boards.

## What is reused rather than rebuilt

| Workbook sheet | Already in the ERP |
|---|---|
| Projects Overview | `projects.Project` |
| Budget → PO value, dates | `finance.ProjectFinance` |
| Budget → Cash In | `finance.PaymentMilestone` |
| Budget → Cash Out | `procurement.budget_status.commitment()` |
| Man Power Status | `hr.Employee` (`iqama_number` is unique, so the duplicated person cannot recur) |
| Sheet3 | nothing — 1,000 rows of gauge machinery |

Project scoping reuses `dashboard.views.projects_visible_to`, which exists because two copies
of that ladder had already drifted.

## Design notes

**Cash In and Cash Out are never typed.** Derived from finance's payment records and
procurement's committed orders. There is no sum range to get wrong, which is the class of bug
that produced the 7.9M understatement. Cash In counts only milestones with an actual receipt
date — an invoice submitted is not cash in, and the workbook's column blurred the two.

**Progress lives only on the leaves.** A parent's figures are its children's, computed rather
than stored. The workbook stored both and the stored copy is what went stale. The update
endpoint refuses a figure on a summary row.

**Two weight conventions, both legitimate.** On MASCO the parent carries the weight (0.10) and
its children divide it (0.05 + 0.05). On the ZULF sheets the parent is blank and the children
carry it all. The rule that holds either way, and the one enforced, is that **the activities
carrying weight sum to 1.00**. A "top-level rows sum to 1.00" rule would put a permanent
warning on half the projects.

**Each update is a row, not an overwrite.** `MilestoneProgressEntry` is append-only, which is
what makes "when was this last updated" answerable and gives progress over time for free.

**Execution milestones are not `finance.PaymentMilestone`.** That is the billing schedule,
seeded during budgeting and locked while finance owns the sheet; the weekly delivery update
cannot sit behind that lock. `ProjectMilestone.payment_milestone` is a nullable FK joining
them, so a milestone can say which invoice it bills against without either side owning the
other.

## The importer

`python manage.py import_milestone_workbook <path.xlsx>` — dry run by default, `--apply` to
write, `--replace` to overwrite a project that already has milestones.

Projects are matched on `proposal_reference`, never on name; name matching is what produced
five spellings of one project. An unmatched or ambiguous reference is **reported, never
guessed**.

The sheets do not share a layout. JAZAN starts at column A and carries an extra `Value (SAR)`;
MASCO starts at column B and does not. Both happen to put something at index 9. Columns are
therefore read from the header row (and the sub-header beneath it, where `Completion Status`
is merged over `Completed`/`Pending`) rather than hardcoded — a fixed map reads the wrong cell
on one layout and produces an import that looks entirely plausible.

Sheet numbering (`S. No`) is used only to tell a parent from a child, never for position: the
MASCO sheet has two rows both labelled 1.1.

## Known data problem found while building this

The ITC sheet (`P02695`) does not add up: `Cable Dressing (racks & field locations)` carries
0.0270 while its children sum to 0.0730, and the activities total 0.9730 rather than 1.0000.
That project can never reach 100%. The importer reports it rather than importing it silently;
somebody who knows the project needs to say which figure is right.

## Verification

- `pmo` suite: 53 tests. Completion is checked against the MASCO sheet's hand-checked 0.522655.
- Every guard mutation-tested: the leaf-only rule, both weight conventions, the tolerance, the
  cash-in receipt rule, the last-updated fallback, the summary-row and range guards on the
  update endpoint, project scoping, and the column-map (hardcoding either layout fails).
- The board's query count is asserted **invariant** across 3 and 9 projects rather than pinned
  to a number.
- The importer was dry-run against the real workbook: 10 of 10 sheets parse, 9 clean, ITC
  reporting the problem above.
