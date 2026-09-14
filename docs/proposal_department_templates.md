# Technical Proposal — Department Section Templates

Source branch: `feature/proposal-department-templates` (app: `proposals`).
Covers the AI / Telecom / Security (Procurement) template feature, the
project autofill on the proposal form, and the related DOCX export fixes
that shipped alongside it.

## 1. What this feature does

Before this feature, a new proposal section could only be pre-filled from
one generic `SectionHeading.default_content` per heading. There was no way
to give the same heading (e.g. "Company Profile") different pre-written
content depending on which department's proposal it's going into.

`SectionHeadingTemplate` adds a second, optional layer: a
**(heading, department)** pair carrying its own rich-text content. The
content editor gets an **AI / Telecom / Security** toggle. Turning one on
swaps the heading picker to that department's own headings, each of which
adds its section already filled with that department's composed content
(text, tables, images) instead of the generic default.

This does **not** replace `SectionHeading.default_content` — proposals/
headings that ignore the toggle behave exactly as before.

## 2. Data model

`proposals/models.py`:

```python
class SectionHeadingTemplate(models.Model):
    DEPARTMENT_CHOICES = [
        ('ai', 'AI'),
        ('telecom', 'Telecom'),
        ('procurement', 'Security'),   # DB value unchanged, label renamed
    ]
    heading = models.ForeignKey(SectionHeading, related_name='dept_templates',
                                 on_delete=models.CASCADE)
    department = models.CharField(max_length=20, choices=DEPARTMENT_CHOICES)
    content = models.TextField(blank=True)   # same rich-HTML format as a section

    class Meta:
        unique_together = ('heading', 'department')
```

- One `SectionHeading` can carry **up to three** `SectionHeadingTemplate`
  rows — one per department — via `unique_together`.
- The `procurement` DB value is kept (existing rows / URL params /
  migrations stay valid); only its **display label** changed to "Security"
  to match the toggle button text. Don't rename the DB value.

### Generic vs. department headings are a partition, not additive

`ProposalEditContentView._context()` (`proposals/views.py`) builds two
separate lists:

- **Generic list** (`headings`): `SectionHeading.objects.filter(is_active=True,
  dept_templates__isnull=True)` — only headings with **no** department
  template at all. Always visible.
- **Department groups** (`dept_headings[dept]['items']`): for each
  department, `SectionHeading.objects.filter(is_active=True,
  dept_templates__department=dept)` — only headings that have a template for
  *that* department. Hidden behind the toggle, shown one group at a time.

A heading that has an AI template disappears from the generic list — it's
only reachable from the AI toggle, pre-filled with the AI content. This was
a deliberate design decision (confirmed with the proposals team), not the
"show everywhere" behavior an earlier draft of this feature had.

## 3. Editor UI (`templates/proposals/proposal_edit_content.html`)

- `#deptToggle`: three buttons (`data-dept="ai|telecom|procurement"`).
  Clicking one:
  1. Deactivates the other two buttons and hides all `.dept-heading-panel`
     blocks, unchecking any boxes inside them (so a stale checked box from a
     previously-open panel can't sneak into the submit).
  2. Activates the clicked button and reveals its panel.
  3. Writes the department code into a hidden `#sectDepartmentField`
     (`name="department"`) that rides along with the "Add sections" form
     submit. Clicking an already-active button toggles it back off
     (clears the hidden field, hides all panels).
- **Select all / Clear** only affects *visible* checkboxes
  (`offsetParent !== null`) — the ones inside hidden department panels are
  excluded, otherwise clicking "Select all" while no toggle is active would
  silently also select every department heading with no `department` set.

## 4. Add-section flow (`add_proposal_section`, `proposals/views.py`)

```
POST proposals:add_section  (heading[]=..., custom_heading=..., department=ai|telecom|procurement|"")
```

For each checked/typed heading:
1. Look up the library `SectionHeading` by name (case-insensitive). If
   missing and the user is a super admin, it's created and added to the
   shared library (unchanged prior behavior).
2. Content defaults to `heading.default_content`.
3. **If** `department` is a valid choice **and** a
   `SectionHeadingTemplate` exists for `(heading, department)` **and** its
   `content` is non-empty → that content is used instead.
4. An unknown/empty `department` value is silently normalized to `''`
   (falls back to generic default) rather than erroring — never a 500 on a
   bad POST value.
5. A **custom** (non-library) heading has nothing to look up regardless of
   `department` — it's created empty, same as before.

## 5. Admin authoring workflow (`proposals/admin.py`)

`SectionHeadingTemplateAdmin` is where a manager/team lead pastes in the
per-department composed content:

- List filterable by `department` and `heading`.
- TinyMCE editor (`code table lists image link` plugins) with
  `relative_urls: False` / `document_base_url: '/'` so pasted image URLs
  stay root-relative, matching the main proposal section editor.

To add a new department template by hand: **Admin → Proposals → Section
heading templates → Add**, pick the library heading, pick the department,
paste the content, save. If the heading doesn't yet exist in the library,
create it first under **Section headings**.

## 6. Seed data (bundled department content)

Four migrations under `proposals/migrations/`:

| Migration | Purpose |
|---|---|
| `0010_sectionheadingtemplate` | Creates the `SectionHeadingTemplate` table |
| `0011_seed_dept_section_templates` | Seeds `seed_data/dept_section_templates.json` (AI: 13, Security: 17, Telecom: 4 headings) + copies `seed_data/dept_template_images/{ai,procurement,telecom}/*` into `default_storage` |
| `0012_alter_sectionheadingtemplate_department` | Renames the `procurement` choice label to "Security" |
| `0013_seed_new_telecom_headings` | Seeds `seed_data/telecom_batch2.json` — 14 more Telecom headings (ICT, Structured Cabling, Core/Distribution/Access Switches, Routers, Data Center, Network Security, Communication Systems, Wireless, Network Management, Time Sync, ICT in an Industrial Plant) |

**Idempotency / name collisions**: `SectionHeading.name` is unique, and
seeding matches by name. If a heading with that exact name already exists
but carries **no** department template (i.e. it's a real, independently
authored generic heading someone added before this feature existed), the
seed disambiguates by suffixing the department label — e.g. `"Company
Profile (AI)"` — rather than silently welding department content onto an
unrelated heading and making it vanish from the generic list. Re-running
the migration finds the heading it already created (it now carries the
matching template) and reuses it.

**Image seeding is best-effort**: a failed upload for one image (transient
storage/network blip) logs a warning and continues rather than failing the
whole migration/deploy — a missing image degrades gracefully at export time
instead of corrupting the deploy.

## 7. DOCX export changes (`proposals/docx_export.py`)

Several fixes landed alongside the template feature, all needed to make the
department content (which is far richer — tables, multiple images — than
what the exporter had been tested against) render correctly:

- **Company name/region correctness across the whole document**
  (`_replace_company_references`): previously only the cover page and
  header textbox got the region-correct company name. Now every mention in
  the body (Confidentiality Notice, Company Overview intro, end-of-document
  Confidentiality section — including inside table cells, via `.iter()` not
  `.findall()`) is replaced too, derived from
  `TechnicalProposal.get_company_name()` / `get_company_acronym()` (single
  source of truth — no second hardcoded region map). Fixes "Arabia" not
  appearing correctly in KSA-region proposals. No-op for Global-region
  proposals.
- **Run-preserving text replacement**: `_replace_in_paragraph_runs` now
  tries replacing within each run independently first (preserving that
  run's own bold/color formatting), only falling back to the old
  join-everything-into-the-first-run behavior if a phrase actually spans a
  run boundary.
- **Table width/alignment fix**: tables now use a fixed
  `BODY_CONTENT_WIDTH_TWIPS` (8831 twips, `w:type="dxa"`) and
  `w:tblInd = BODY_LEFT_INDENT_TWIPS` (709 twips) instead of `w:type="pct"`
  with no indent — this aligns generated tables with the body-text left
  indent so they don't run under the header's vertical sidebar labels.
  **Important constraint**: `w:tblInd` is only honored on a left-aligned
  table — `w:jc="center"` makes Word ignore it, so `w:jc` is deliberately
  left unset.
- **Fixed pasted-image size**: `IMAGE_WIDTH_EMU`/`IMAGE_HEIGHT_EMU` render
  every pasted image at a fixed 320×160px (2:1) instead of the previous
  600×300px, which was wider than the usable text column and overflowed
  past the right margin/sidebar.
- **Image disclaimer caption**: every image paragraph now gets a trailing
  "This image is for representation purposes only" caption
  (`IMAGE_DISCLAIMER_TEXT`), in addition to any user-supplied figure
  caption.
- **Missing images degrade gracefully**: an unreadable/missing image used
  to leave a dangling `r:embed` placeholder that made Word refuse to open
  the whole document. Now the entire `<w:r>...<w:drawing>...</w:drawing>
  </w:r>` run for that image is stripped instead.

## 8. Project autofill on the New/Edit Proposal form

`_project_autofill_map()` (`proposals/views.py`) builds
`{project_id: {reference, client}}` from the same (region-scoped) project
queryset already shown in the Project dropdown, and is passed to
`templates/proposals/proposal_form.html` via `json_script`.

- `Project.proposal_reference` (up to 255 chars, e.g. `"LNA 1234 - Some Long
  Project Name"`) doesn't fit `TechnicalProposal.proposal_reference`
  (max 50 chars, unique). The map derives the short `"LNA ####"` form via
  `parse_lna_reference()` when recognizable; otherwise uses the raw value if
  it's ≤ 50 chars, or omits it from the map entirely (JS leaves the field
  untouched) rather than risk a validation failure.
- JS only overwrites Reference/Client Name if the field's current value
  still equals what autofill **itself** last wrote (`lastAutofilled`
  tracked in JS, starting empty — not the field's initial value). This
  means: switching projects re-autofills cleanly, but a value the user
  typed by hand (or a saved value on the edit page, which autofill never
  wrote) is never silently overwritten.

## 9. Test coverage (`proposals/tests.py`)

| Class | Covers |
|---|---|
| `SectionHeadingTemplateModelTests` | `__str__`, unique-together enforcement, multiple departments per heading allowed |
| `AddSectionDepartmentTemplateTests` | department content used when present; falls back to generic default when absent; empty/unknown department is a no-op, not a 500; custom headings with a department don't error; CSRF enforced |
| `EditContentViewDepartmentGroupingTests` | generic list excludes any heading with a department template; each department group only shows its own headings; label "Security" for `procurement`; inactive headings excluded from both generic and department groups |
| `DeptTemplateSeedMigrationTests` / `NewTelecomHeadingsSeedMigrationTests` | seed migrations create the expected headings/templates, are idempotent, and disambiguate name collisions |
| `ProjectAutofillTests` | autofill map derivation and short-reference fallback rules |
| `ProposalDocxExportTests` | company-name/region replacement, table sizing, image handling in the exporter |

## 10. Runbook: adding a new department template

1. In **Admin → Proposals → Section headings**, create the heading if it
   doesn't already exist in the library (or reuse an existing one that
   currently has **no** department template — reusing one that already has
   a template for a *different* department is fine and expected).
2. In **Admin → Proposals → Section heading templates → Add**, pick the
   heading + department (AI / Telecom / Security), paste the composed
   content (TinyMCE — text, tables, images all supported).
3. Save. The heading now moves out of the generic "always visible" list and
   into that department's toggle panel in the content editor — for every
   proposal, immediately, no code change or migration needed.
4. To seed content in bulk instead (many headings at once, e.g. a new batch
   of Telecom sections): add a JSON file to
   `proposals/migrations/seed_data/` in the same shape as
   `dept_section_templates.json` (`heading`, `heading_order`, `department`,
   `content`), plus any images under
   `seed_data/dept_template_images/<department>/`, and add a new
   `RunPython` migration modeled on `0013_seed_new_telecom_headings.py`.
