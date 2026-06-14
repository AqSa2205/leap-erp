# Costing Stage Barriers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Enforce strict stage-based edit barriers on costing sheets (super_admin override; `ready_for_costing` fully locked; new sheets only via `enforce_stage_barriers`). KPI-grade accuracy.

**Spec:** `docs/superpowers/specs/2026-06-14-costing-stage-barriers-design.md`

**Tech stack:** Django 6, `python manage.py test` (NOT pytest), Windows.

**Key facts:**
- `_user_can_edit_sheet(user, sheet)` in `costing/views.py` (~line 182) is the single gate ~21 mutation endpoints call.
- `CostingSheet.workflow_stage` choices: `bom_in_progress`, `ready_for_costing`, `costing_in_progress`, `finalized`, `finance_review`, `finance_approved`. Default `bom_in_progress`.
- User team props: `is_proposal_team_user` (proposal_head/rep), `is_sales_team_user` (sales_rep/manager/admin/super_admin), `is_finance_team_user` (finance_*), `is_super_admin_user`, `is_admin_user`, `is_manager_user`.
- Role name constants on `accounts.models.Role`: `SUPER_ADMIN`, `ADMIN`, `MANAGER`, `SALES_REP`, `PROPOSAL_REP`, `FINANCE_REP`.
- `costing/tests.py` already has `CostingProjectAutofillTests` (append new classes at the END).
- Latest costing migration must be checked with `python manage.py makemigrations costing --dry-run` before writing.

**Shared test setup (reuse in test tasks):**
```python
from django.test import TestCase
from accounts.models import Role, User
from projects.models import Region, ProjectStatus, Project
from costing.models import CostingSheet


def _user(username, role_name, region):
    role, _ = Role.objects.get_or_create(name=role_name)
    u = User.objects.create_user(username, password='x')
    u.role = role
    u.region = region
    u.save()
    return u


class _BarrierBase(TestCase):
    def setUp(self):
        self.region = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        self.project = Project.objects.create(
            project_name='P', proposal_reference='REF-1',
            status=self.status, region=self.region)
        self.superadmin = _user('sa', Role.SUPER_ADMIN, self.region)
        self.proposal = _user('pr', Role.PROPOSAL_REP, self.region)
        self.sales = _user('sr', Role.SALES_REP, self.region)
        self.finance = _user('fr', Role.FINANCE_REP, self.region)

    def _sheet(self, stage, strict=True, **kw):
        return CostingSheet.objects.create(
            title='S', project=self.project, created_by=kw.pop('creator', self.proposal),
            workflow_stage=stage, enforce_stage_barriers=strict, **kw)
```

---

## Task 1: `enforce_stage_barriers` field + migrations

**Files:** `costing/models.py`, two migrations, `costing/tests.py`

- [ ] **Step 1: Failing test** — append at END of `costing/tests.py`:
```python
class EnforceFlagTests(TestCase):
    def test_new_sheet_defaults_strict(self):
        from projects.models import Region, ProjectStatus, Project
        from accounts.models import User
        region = Region.objects.create(name='R', code='LNA', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='active')
        proj = Project.objects.create(project_name='P', proposal_reference='REF-X',
                                      status=status, region=region)
        u = User.objects.create_user('u', password='x')
        from costing.models import CostingSheet
        sheet = CostingSheet.objects.create(title='S', project=proj, created_by=u)
        self.assertTrue(sheet.enforce_stage_barriers)
```

- [ ] **Step 2: Run** `python manage.py test costing.tests.EnforceFlagTests -v 2` → FAIL (no field).

- [ ] **Step 3: Implement** — in `costing/models.py` `CostingSheet`, add (near `workflow_stage`):
```python
    enforce_stage_barriers = models.BooleanField(
        default=True,
        help_text='When set, editing is locked to the team that owns the current '
                  'workflow stage (KPI mode). Pre-existing sheets are False so they '
                  'keep the older lenient rules.')
```

- [ ] **Step 4: Schema migration** — `python manage.py makemigrations costing` (adds the field). Apply with `migrate`.

- [ ] **Step 5: Data migration** — `python manage.py makemigrations costing --empty --name grandfather_existing_sheets`, then edit it to:
```python
from django.db import migrations


def grandfather(apps, schema_editor):
    CostingSheet = apps.get_model('costing', 'CostingSheet')
    CostingSheet.objects.update(enforce_stage_barriers=False)


def ungrandfather(apps, schema_editor):
    pass  # one-way: re-enabling barriers on old sheets is a deliberate manual act


class Migration(migrations.Migration):
    dependencies = [('costing', '<the schema migration from step 4>')]
    operations = [migrations.RunPython(grandfather, ungrandfather)]
```
Apply with `migrate`. (Set the dependency to the actual schema-migration name created in Step 4.)

- [ ] **Step 6: Run** the test → PASS. `python manage.py check`. `python manage.py makemigrations --check --dry-run` → No changes.

- [ ] **Step 7: Commit**
```
git add costing/models.py costing/migrations/ costing/tests.py
git commit -m "Costing: add enforce_stage_barriers flag (grandfather existing sheets)"
```

---

## Task 2: `_strict_stage_edit` + rewire `_user_can_edit_sheet` (server gate)

**Files:** `costing/views.py`, `costing/tests.py`

- [ ] **Step 1: Failing tests** — append at END of `costing/tests.py` (uses `_BarrierBase` + `_user` from the plan header — copy those helpers into tests.py near the top if not already present, or inline them in this class's setUp):
```python
class StrictGateTests(_BarrierBase):
    def _can(self, user, sheet):
        from costing.views import _user_can_edit_sheet
        return _user_can_edit_sheet(user, sheet)

    def test_bom_stage_proposal_only(self):
        s = self._sheet('bom_in_progress')
        self.assertTrue(self._can(self.proposal, s))
        self.assertFalse(self._can(self.sales, s))
        self.assertTrue(self._can(self.superadmin, s))

    def test_ready_for_costing_locked_for_all(self):
        s = self._sheet('ready_for_costing')
        self.assertFalse(self._can(self.proposal, s))
        self.assertFalse(self._can(self.sales, s))
        self.assertTrue(self._can(self.superadmin, s))  # only override

    def test_costing_stage_sales_only(self):
        s = self._sheet('costing_in_progress')
        self.assertTrue(self._can(self.sales, s))
        self.assertFalse(self._can(self.proposal, s))

    def test_costing_stage_sales_out_of_region_blocked(self):
        from projects.models import Region
        other = Region.objects.create(name='UK', code='UK', currency='GBP')
        self.sales.region = other; self.sales.save()
        s = self._sheet('costing_in_progress')
        self.assertFalse(self._can(self.sales, s))

    def test_finance_stage_unchanged(self):
        s = self._sheet('finance_review')
        self.assertTrue(self._can(self.finance, s))
        self.assertFalse(self._can(self.sales, s))

    def test_grandfathered_sheet_keeps_lenient_rules(self):
        # Non-strict sheet: proposal can edit even at costing_in_progress (legacy).
        s = self._sheet('costing_in_progress', strict=False)
        self.assertTrue(self._can(self.proposal, s))
```

- [ ] **Step 2: Run** `python manage.py test costing.tests.StrictGateTests -v 2` → multiple FAIL.

- [ ] **Step 3: Implement** — in `costing/views.py`, add `_strict_stage_edit` just above `_user_can_edit_sheet`, and rewrite `_user_can_edit_sheet`'s pre-finance section to branch on `sheet.enforce_stage_barriers`:
```python
def _strict_stage_edit(user, sheet, stage):
    """Edit rights under the strict per-stage barriers (KPI mode).
    super_admin is handled by the caller; region scope mirrors the app."""
    region_ok = bool(sheet.project and sheet.project.region_id == user.region_id)
    if stage == 'bom_in_progress':
        return bool(getattr(user, 'is_proposal_team_user', False))
    if stage == 'ready_for_costing':
        return False  # locked checkpoint — Start costing unlocks it
    if stage in ('costing_in_progress', 'finalized'):
        return bool(getattr(user, 'is_sales_team_user', False) and region_ok)
    return False
```
Then in `_user_can_edit_sheet`, after the `finance_review` / `finance_approved` blocks and before the legacy creator/admin/manager/proposal block, insert:
```python
    if sheet.enforce_stage_barriers:
        return _strict_stage_edit(user, sheet, stage)
```
Leave the existing legacy block (creator/admin/manager/proposal) intact below it for grandfathered sheets. Update the function docstring to mention the strict-barrier branch.

- [ ] **Step 4: Run** the test → all PASS. Then `python manage.py test costing -v 1` (no regressions). `python manage.py check`.

- [ ] **Step 5: Commit**
```
git add costing/views.py costing/tests.py
git commit -m "Costing: strict per-stage edit barriers in _user_can_edit_sheet"
```

---

## Task 3: Endpoint-level integration tests (defense confirmed end-to-end)

**Files:** `costing/tests.py`

- [ ] **Step 1: Tests** — append at END. Use a real mutation endpoint. `ajax_add_line_item` needs a section; simpler to use `ajax_update_sheet_params` (POST `costing:update_params` with a non-pricing field? it has none) — instead use `ajax_add_sow_item` (POST `costing:add_sow_item`, body `description`, `quantity`). Confirm the url name + payload by reading the view first. Pattern:
```python
class BarrierEndpointTests(_BarrierBase):
    def _add_sow(self, user, sheet):
        self.client.force_login(user)
        return self.client.post(
            reverse('costing:add_sow_item', kwargs={'pk': sheet.pk}),
            {'description': 'Cabling works', 'quantity': '1'})

    def test_sales_blocked_during_bom(self):
        s = self._sheet('bom_in_progress')
        self.assertEqual(self._add_sow(self.sales, s).status_code, 403)

    def test_proposal_allowed_during_bom(self):
        s = self._sheet('bom_in_progress')
        self.assertEqual(self._add_sow(self.proposal, s).status_code, 200)

    def test_ready_for_costing_locked(self):
        s = self._sheet('ready_for_costing')
        self.assertEqual(self._add_sow(self.sales, s).status_code, 403)
        self.assertEqual(self._add_sow(self.proposal, s).status_code, 403)

    def test_sales_allowed_after_start_costing(self):
        s = self._sheet('costing_in_progress')
        self.assertEqual(self._add_sow(self.sales, s).status_code, 200)
        self.assertEqual(self._add_sow(self.proposal, s).status_code, 403)
```
(Read `ajax_add_sow_item` first to confirm `reverse` name, required POST keys, and that it returns 200 JSON on success / 403 on gate fail. Adjust the payload/url to match reality. If `add_sow_item` is awkward, substitute another `_user_can_edit_sheet`-gated endpoint with a simple payload and note the choice.)

- [ ] **Step 2: Run** `python manage.py test costing.tests.BarrierEndpointTests -v 2` → PASS.

- [ ] **Step 3: Commit**
```
git add costing/tests.py
git commit -m "Costing: end-to-end stage-barrier endpoint tests"
```

---

## Task 4: Detail view `can_edit` + read-only banner

**Files:** `costing/views.py` (`CostingDetailView.get_context_data`), `templates/costing/costing_detail.html`, `costing/tests.py`

- [ ] **Step 1: Test** — append:
```python
class DetailCanEditContextTests(_BarrierBase):
    def test_context_can_edit_per_role(self):
        s = self._sheet('bom_in_progress')
        self.client.force_login(self.sales)
        resp = self.client.get(reverse('costing:detail', kwargs={'pk': s.pk}))
        self.assertFalse(resp.context['can_edit'])
        self.client.force_login(self.proposal)
        resp = self.client.get(reverse('costing:detail', kwargs={'pk': s.pk}))
        self.assertTrue(resp.context['can_edit'])
```

- [ ] **Step 2: Run** → FAIL (no `can_edit`).

- [ ] **Step 3: Implement** — in `CostingDetailView.get_context_data` (before `return context`), add:
```python
        context['can_edit'] = _user_can_edit_sheet(self.request.user, sheet)
        context['edit_lock_reason'] = _edit_lock_reason(self.request.user, sheet)
```
And add a module-level helper `_edit_lock_reason(user, sheet)` near `_user_can_edit_sheet` returning a short string when the user cannot edit due to the stage barrier (empty string when they can edit). Example:
```python
def _edit_lock_reason(user, sheet):
    if _user_can_edit_sheet(user, sheet):
        return ''
    if not getattr(sheet, 'enforce_stage_barriers', False):
        return ''  # legacy reasons (finance locks) already have their own banners
    stage = sheet.workflow_stage
    if stage == 'bom_in_progress':
        return 'The Proposal team is still building this BOM. It unlocks for Sales after handover and "Start costing".'
    if stage == 'ready_for_costing':
        return 'Handed to Sales. Click "Start costing" to begin — the sheet is locked until then.'
    if stage in ('costing_in_progress', 'finalized'):
        return 'Sales owns this sheet at its current stage — read-only for other teams.'
    return ''
```

- [ ] **Step 4: Banner** — in `templates/costing/costing_detail.html`, near the top of the content (above the BOM/sections area, after the workflow banner block ~line 250), add:
```django
{% if not can_edit and edit_lock_reason %}
<div class="alert alert-secondary d-flex align-items-center" role="alert">
    <i class="bi bi-lock-fill me-2"></i>
    <div><strong>Read-only.</strong> {{ edit_lock_reason }}</div>
</div>
{% endif %}
```

- [ ] **Step 5: Run** the test → PASS. `python manage.py check`.

- [ ] **Step 6: Commit**
```
git add costing/views.py templates/costing/costing_detail.html costing/tests.py
git commit -m "Costing: detail page can_edit flag + stage read-only banner"
```

---

## Task 5: Hide primary edit controls when read-only

**Files:** `templates/costing/costing_detail.html`

- [ ] **Step 1:** Read the template and wrap the primary edit entry points in `{% if can_edit %}…{% endif %}` so read-only users don't see dead controls. Cover at least: "Add Section", "Add line item"/paste/import toolbars, "Apply default currency", Scope-of-Work add row, vendor-quote upload control, and the "Edit sheet" link. Do NOT gate read-only affordances (export PDF/Excel, view, change-log). Leave the workflow transition buttons (Hand over / Start costing / Finalize / …) untouched — they are gated separately and must remain visible.

- [ ] **Step 2: Manual check** — render the page for a sales user on a `bom_in_progress` strict sheet: the add/edit toolbars are hidden, the read-only banner shows, the "Start costing"/transition area still shows where applicable. For a proposal user on the same sheet: edit controls visible.

- [ ] **Step 3:** `python manage.py test costing -v 1` (no template errors). `python manage.py check`.

- [ ] **Step 4: Commit**
```
git add templates/costing/costing_detail.html
git commit -m "Costing: hide edit controls on read-only (stage-locked) sheets"
```

---

## Task 6: Regression + final review

- [ ] `python manage.py test costing -v 1` and `python manage.py test -v 1` (no new failures).
- [ ] `python manage.py check` and `python manage.py makemigrations --check --dry-run` (No changes).
- [ ] Final holistic review (opus): verify the gate is airtight (every mutation endpoint funnels through `_user_can_edit_sheet`), super_admin override works, `ready_for_costing` is fully locked, grandfathered sheets unaffected, finance locks unchanged, and no endpoint bypasses the gate. SHIP / FIX-FIRST verdict.

---

## Self-review notes
- **Spec coverage:** flag+migration (T1); strict gate + super_admin override + ready_for_costing lock + grandfather parity (T2); end-to-end 403/200 (T3); UI can_edit + banner (T4); hide controls (T5); regression+review (T6).
- **Naming:** `enforce_stage_barriers`, `_strict_stage_edit`, `_edit_lock_reason`, `can_edit`, `edit_lock_reason` used consistently across model/view/template/tests.
- **No schema beyond T1:** the gate is logic-only; only T1 migrates.
