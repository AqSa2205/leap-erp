# Revision Snapshot Samples

Sample JSON snapshots showing exactly what would be captured when a user
clicks "Create New Revision" on each type of record. These are static examples
for review only — no feature is built yet.

## Files

- **`commercial_proposal_R00.json`** — a Project record (from the `projects`
  app, now labelled "Commercial Proposals" in the UI). Stores the live
  metadata and narrative.
- **`costing_sheet_R00.json`** — a CostingSheet with its sections, line items,
  section-level rate overrides, Scope of Work items, selected T&C templates,
  and exchange rates used at the time.
- **`technical_proposal_R00.json`** — a TechnicalProposal with rich-text
  content for all 10 sections, engineering documents list, plus pointers to
  the frozen DOCX/PDF files saved under `revisions/.../R00/`.
- **`pqd_R00.json`** — a PrequalificationDocument with all 7 sections, each
  attachment's metadata and the path to its preserved file copy, plus the
  frozen merged PDF path.

## Guarantee

When a revision is created, the system also copies any relevant file
attachments (costing PDF export, technical proposal DOCX, PQD attachments +
merged PDF) into `MEDIA_ROOT/revisions/<kind>/<pk>/<R00>/`. Those files are
never overwritten, so downloading R00 one year later produces the exact
bytes from when the revision was cut.

## What's NOT captured

- Activity timeline / comments (live data, not part of the snapshot)
- Status change history (separate `StatusHistory` model already tracks this)
- External portal URLs (stay live)
- User permissions / role assignments
