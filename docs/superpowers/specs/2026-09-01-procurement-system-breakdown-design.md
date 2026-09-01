# Procurement — Project System Breakdown — Design

Date: 2026-09-01
Module: `procurement`
Status: built

## Why

Procurement keeps a workbook per project: every ordered line grouped under its
system, with each line's share of the project, its share of its own system, and
how much has been delivered. It is maintained by hand from the PO list, which
means it is out of date the moment a PO or a delivery note changes, and only one
person has it.

The ERP already holds every input — `PurchaseOrderItem.system`, line values, and
`DeliveryNoteItem.source_po_item` linking deliveries back to the ordered line.
The sheet is a report, not a data source.

## The four percentages use three denominators

This is the part to get right. The columns are not four views of one number:

| Column | Numerator | Denominator |
|---|---|---|
| **% of All** | line value | project total |
| **% of system** | line value | that system's total |
| **% Delivery** | delivered value | **project total** |
| **Pending %** | undelivered value | **project total** |

Which gives the identity the sheet rests on:

```
% Delivery + Pending %  ==  % of All        (every row)
```

Delivery and pending are shares of the *project*, not of the line, so those two
columns read straight down the page as progress against the whole job. Measuring
delivery against the line instead would make every fully-delivered row show
100% and destroy that reading — it is the most natural wrong choice here, so it
has its own test.

Confirmed against the source workbook: Siren 9.23 + 1.24 ≈ 10.46; CCTV 10.48 +
7.84 ≈ 18.33.

## What counts as ordered

Only committed orders — `issued`, `client_acknowledged`, `completed` — reusing
`budget_status.COMMITTED_STATUSES` so this page and the budget percentage cannot
disagree about what "ordered" means.

A **draft** has not been ordered, so its delivery progress is not a fact about
anything. A **cancelled** order is not part of the project's composition at all.
When a project has only drafts the page says so explicitly, because an empty
table otherwise reads as broken rather than as "nothing ordered yet".

## Awkward data, and what is done about it

**A line with no system** goes into *Not categorised* rather than being dropped —
dropping it would lose ordered value from a page whose entire premise is that the
shares total 100%. It sorts last however large it is: it is a gap to close, not a
finding about the project.

**More delivered than ordered** is a data problem, not extra value. Left uncapped
it drives Pending negative and pushes % Delivery past % of All, quietly breaking
the identity above. The delivered value is capped at the line value and the
affected lines are named in a banner so somebody fixes the delivery note.

**Mixed currencies.** Lines are converted to SAR before being compared; otherwise
a 100 USD line and a 100 SAR line would look like equal shares when one is nearly
four times the other. Any conversion is flagged, since current rates are being
applied to historic orders.

**A delivery note line not linked to a PO item** is not attributed to anything.
Guessing would credit delivery against a line that never received it.

**Nothing ordered** yields `None` percentages, not `0` — on a page where 0% means
"none of this delivered", using it for "there is nothing here" makes two very
different situations identical. The template renders an em dash.

## Where it lives

`/procurement/project/<id>/systems/`, reached from the **Systems** button on each
project card in POs by Project.

Visibility follows `_visible_pos_for()`, so the page can never show a PO the flat
list would not. A viewer with no PO against the project gets 403 rather than an
empty page — rendering one would confirm the project exists and name it, which is
the same disclosure the board avoids by building itself from visible POs only.

## Scope

- No new models, no migration. Everything is derived.
- Delivered quantities are fetched for the whole page in one aggregate query.
- No change to how POs, deliveries or systems are entered.
