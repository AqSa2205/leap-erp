# Team Activity Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a super-admin "Team Activity" review to the `kpis` app — an overview table of every user's cross-module activity counts (pipelines, BOMs, sales handovers, POs, proposals, tasks) plus a per-user drill-in, period-scoped, computed live from existing attribution fields.

**Architecture:** A code registry (`ACTIVITY_METRICS`) declares each countable action as `(model, actor field, date field)`. A service runs one grouped `COUNT … GROUP BY actor` query per metric and assembles per-user rows. Two views (overview table + per-user detail) render it, gated by a new `kpis.activity` capability. No new tables — reads existing `created_by`/handover fields, so it works on all historical data with no backfill.

**Tech Stack:** Django 6, PostgreSQL, Django test runner (`python manage.py test`), Bootstrap 5 templates. Windows / PowerShell + Bash available.

## Global Constraints

- All changes go on the `dev` branch. Do NOT merge to `main` until the user explicitly says so.
- Tests run with `python manage.py test` (NOT pytest).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Follow the existing `kpis` app patterns (registry → service → thin view), and the capability RBAC in `accounts/permissions.py` (capability declared in code, grants seeded by a data migration calling `seed_default_permissions()`).
- The whole feature is super_admin-only via the new `kpis.activity` capability.

---

## File Structure

- Create `kpis/activity.py` — `ActivityMetric` dataclass, `ACTIVITY_METRICS` registry, `MODULE_ORDER`.
- Create `kpis/activity_service.py` — `activity_window`, `build_activity_overview`, `build_user_activity`, `activity_period_options`.
- Modify `kpis/views.py` — add `activity_overview`, `activity_detail` views.
- Modify `kpis/urls.py` — add two routes.
- Create `kpis/templates/kpis/activity_overview.html`, `kpis/templates/kpis/activity_detail.html`.
- Modify `accounts/permissions.py` — declare `kpis.activity`, grant to super_admin.
- Create `accounts/migrations/0025_seed_kpis_activity_permission.py`.
- Modify `templates/base.html` — "Team Activity" nav link in the Department KPIs section.
- Modify `kpis/tests.py` — append `ActivityRegistryTests`, `ActivityServiceTests`, `ActivityViewTests`.

---

## Task 1: Activity registry

**Files:**
- Create: `kpis/activity.py`
- Test: `kpis/tests.py` (append `ActivityRegistryTests`)

**Interfaces:**
- Produces:
  - `ActivityMetric` dataclass with fields `key, module, label, model_path, actor_field, date_field, headline` and methods `counts(start, end) -> dict[int, int]` (maps user_id → count) and `count_for(start, end, user_id) -> int`. `start`/`end` are `datetime.date` or `None` (None = all-time).
  - `ACTIVITY_METRICS: list[ActivityMetric]` (21 metrics).
  - `MODULE_ORDER: list[str]` = `['Pipeline', 'Costing', 'Procurement', 'Proposals', 'Dev Tracking']`.
  - `headline_metrics() -> list[ActivityMetric]`.

- [ ] **Step 1: Write the failing test**

Append to `kpis/tests.py`:

```python
class ActivityRegistryTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name=Role.SALES_REP)
        self.u1 = User.objects.create_user('act1', password='pw', role=self.role)
        self.u2 = User.objects.create_user('act2', password='pw', role=self.role)
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')

    def _make_project(self, ref, creator, when):
        from django.utils import timezone
        p = Project.objects.create(
            project_name=ref, proposal_reference=ref, status=self.status,
            region=self.region, created_by=creator)
        Project.objects.filter(pk=p.pk).update(
            created_at=timezone.make_aware(datetime.datetime(when.year, when.month, when.day, 12)))
        return p

    def test_projects_created_counts_by_user_and_period(self):
        from kpis.activity import ACTIVITY_METRICS
        metric = next(m for m in ACTIVITY_METRICS if m.key == 'projects_created')
        self._make_project('P1', self.u1, datetime.date(2026, 5, 1))
        self._make_project('P2', self.u1, datetime.date(2026, 5, 2))
        self._make_project('P3', self.u2, datetime.date(2026, 5, 3))
        self._make_project('P4', self.u1, datetime.date(2025, 1, 1))   # prior year
        # All-time
        counts = metric.counts(None, None)
        self.assertEqual(counts[self.u1.id], 3)
        self.assertEqual(counts[self.u2.id], 1)
        # Q2 2026 window
        q2 = metric.counts(datetime.date(2026, 4, 1), datetime.date(2026, 7, 1))
        self.assertEqual(q2[self.u1.id], 2)
        self.assertEqual(q2.get(self.u2.id), 1)
        # count_for one user
        self.assertEqual(metric.count_for(None, None, self.u1.id), 3)

    def test_registry_has_21_metrics_and_headlines(self):
        from kpis.activity import ACTIVITY_METRICS, headline_metrics
        self.assertEqual(len(ACTIVITY_METRICS), 21)
        self.assertEqual({m.key for m in headline_metrics()}, {
            'projects_created', 'boms_created', 'sales_finalised',
            'handed_to_finance', 'pos_created', 'tech_proposals', 'tasks_completed'})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test kpis.tests.ActivityRegistryTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'kpis.activity'`.

- [ ] **Step 3: Write `kpis/activity.py`**

```python
"""Registry of countable user actions across the ERP, for the Team Activity
review. Each metric is one grouped COUNT query over an existing model, keyed by
an actor FK and time-scoped by a date field. No new tables — reads the
created_by / handover fields already on the records.
"""
from dataclasses import dataclass

from django.apps import apps
from django.db.models import Count


MODULE_ORDER = ['Pipeline', 'Costing', 'Procurement', 'Proposals', 'Dev Tracking']


@dataclass(frozen=True)
class ActivityMetric:
    key: str
    module: str
    label: str
    model_path: str      # 'projects.Project'
    actor_field: str     # 'created_by'
    date_field: str      # 'created_at'
    headline: bool = False

    def _base(self, start, end, user_id=None):
        Model = apps.get_model(self.model_path)
        qs = Model.objects.filter(**{f'{self.actor_field}__isnull': False})
        if user_id is not None:
            qs = qs.filter(**{self.actor_field: user_id})
        if start is not None:
            qs = qs.filter(**{f'{self.date_field}__date__gte': start,
                              f'{self.date_field}__date__lt': end})
        return qs

    def counts(self, start, end):
        """{user_id: count} grouped by actor over the window (None = all-time)."""
        rows = self._base(start, end).values(self.actor_field).annotate(n=Count('id'))
        return {r[self.actor_field]: r['n'] for r in rows}

    def count_for(self, start, end, user_id):
        return self._base(start, end, user_id=user_id).count()


ACTIVITY_METRICS = [
    # Pipeline
    ActivityMetric('projects_created', 'Pipeline', 'Pipelines created',
                   'projects.Project', 'created_by', 'created_at', headline=True),
    ActivityMetric('status_changes', 'Pipeline', 'Status changes made',
                   'projects.ProjectHistory', 'changed_by', 'changed_at'),
    ActivityMetric('documents_uploaded', 'Pipeline', 'Documents uploaded',
                   'projects.Document', 'uploaded_by', 'uploaded_at'),
    ActivityMetric('project_revisions', 'Pipeline', 'Proposal revisions',
                   'projects.ProjectRevision', 'created_by', 'created_at'),
    # Costing
    ActivityMetric('boms_created', 'Costing', 'BOMs created',
                   'costing.CostingSheet', 'created_by', 'created_at', headline=True),
    ActivityMetric('boms_handed_to_sales', 'Costing', 'Handed to sales',
                   'costing.CostingSheet', 'handed_over_by', 'handed_over_at'),
    ActivityMetric('costing_started', 'Costing', 'Costing started',
                   'costing.CostingSheet', 'costing_started_by', 'costing_started_at'),
    ActivityMetric('sales_finalised', 'Costing', 'Sales finalised',
                   'costing.CostingSheet', 'finalized_by', 'finalized_at', headline=True),
    ActivityMetric('handed_to_finance', 'Costing', 'Handed to finance',
                   'costing.CostingSheet', 'finance_review_by', 'finance_review_at', headline=True),
    ActivityMetric('finance_approved', 'Costing', 'Finance approved',
                   'costing.CostingSheet', 'finance_approved_by', 'finance_approved_at'),
    # Procurement
    ActivityMetric('pos_created', 'Procurement', 'POs created',
                   'procurement.PurchaseOrder', 'created_by', 'created_at', headline=True),
    ActivityMetric('po_scm_approvals', 'Procurement', 'PO SCM approvals',
                   'procurement.PurchaseOrder', 'scm_approved_by', 'scm_approved_at'),
    ActivityMetric('po_pm_approvals', 'Procurement', 'PO PM approvals',
                   'procurement.PurchaseOrder', 'pm_approved_by', 'pm_approved_at'),
    ActivityMetric('po_coo_approvals', 'Procurement', 'PO COO approvals',
                   'procurement.PurchaseOrder', 'coo_approved_by', 'coo_approved_at'),
    ActivityMetric('po_ceo_approvals', 'Procurement', 'PO CEO approvals',
                   'procurement.PurchaseOrder', 'ceo_approved_by', 'ceo_approved_at'),
    ActivityMetric('delivery_notes', 'Procurement', 'Delivery notes',
                   'procurement.DeliveryNote', 'created_by', 'created_at'),
    ActivityMetric('inventory_reports', 'Procurement', 'Inventory reports',
                   'procurement.InventoryReport', 'created_by', 'created_at'),
    # Proposals
    ActivityMetric('tech_proposals', 'Proposals', 'Technical proposals',
                   'proposals.TechnicalProposal', 'created_by', 'created_at', headline=True),
    ActivityMetric('pqds', 'Proposals', 'Prequalification docs',
                   'proposals.PrequalificationDocument', 'created_by', 'created_at'),
    # Dev Tracking
    ActivityMetric('stacks_created', 'Dev Tracking', 'Task stacks created',
                   'devtracking.TaskStack', 'created_by', 'created_at'),
    ActivityMetric('tasks_completed', 'Dev Tracking', 'Tasks completed',
                   'devtracking.DevTask', 'developer', 'completed_at', headline=True),
]


def headline_metrics():
    return [m for m in ACTIVITY_METRICS if m.headline]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test kpis.tests.ActivityRegistryTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add kpis/activity.py kpis/tests.py
git commit -m "kpis: activity metric registry (cross-module action counts)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `kpis.activity` capability + seed migration

**Files:**
- Modify: `accounts/permissions.py` (add to `CAPABILITIES`; add to `DEFAULT_CODENAME_GRANTS['super_admin']`)
- Create: `accounts/migrations/0025_seed_kpis_activity_permission.py`
- Test: `kpis/tests.py` (append `ActivityPermissionSeedTests`)

**Interfaces:**
- Produces: enforced capability codename `kpis.activity`, seeded ON for `super_admin` only.

- [ ] **Step 1: Write the failing test**

Append to `kpis/tests.py`:

```python
class ActivityPermissionSeedTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _allowed(self, role_name):
        role = Role.objects.get(name=role_name)
        return RolePermission.objects.get(role=role, codename='kpis.activity').allowed

    def test_activity_cap_super_admin_only(self):
        self.assertTrue(self._allowed(Role.SUPER_ADMIN))
        for r in (Role.ADMIN, Role.MANAGER, Role.SALES_REP, Role.AI_HEAD):
            self.assertFalse(self._allowed(r), msg=r)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test kpis.tests.ActivityPermissionSeedTests -v 2`
Expected: FAIL — `RolePermission.DoesNotExist` (codename `kpis.activity` not declared yet).

- [ ] **Step 3: Declare the capability and grant**

In `accounts/permissions.py`, find the KPI block in `CAPABILITIES`:

```python
    *_module('kpis', 'Department KPIs'),
    Capability('kpis.manage', 'Department KPIs', 'manage',
               'Enter KPI values & targets', enforced=True, order=2),
]
```

Replace with:

```python
    *_module('kpis', 'Department KPIs'),
    Capability('kpis.manage', 'Department KPIs', 'manage',
               'Enter KPI values & targets', enforced=True, order=2),
    Capability('kpis.activity', 'Department KPIs', 'activity',
               'View team activity review', enforced=True, order=3),
]
```

In `DEFAULT_CODENAME_GRANTS`, change the super_admin line:

```python
    'super_admin':  {'devtracking.admin', 'devtracking.mywork', 'kpis.manage'},
```

to:

```python
    'super_admin':  {'devtracking.admin', 'devtracking.mywork', 'kpis.manage', 'kpis.activity'},
```

- [ ] **Step 4: Create the seed migration**

Create `accounts/migrations/0025_seed_kpis_activity_permission.py`:

```python
from django.db import migrations


def seed(apps, schema_editor):
    # Adds the kpis.activity rows for every role at baseline (super_admin ON,
    # rest OFF). Idempotent (get_or_create) — only the missing rows are created.
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.filter(codename='kpis.activity').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0024_restrict_kpis_to_super_admin'),
    ]
    operations = [migrations.RunPython(seed, unseed)]
```

- [ ] **Step 5: Apply the migration and run the test**

Run: `python manage.py migrate accounts && python manage.py test kpis.tests.ActivityPermissionSeedTests -v 2`
Expected: migration `0025_seed_kpis_activity_permission... OK`; test PASS.

- [ ] **Step 6: Commit**

```bash
git add accounts/permissions.py accounts/migrations/0025_seed_kpis_activity_permission.py kpis/tests.py
git commit -m "kpis: add kpis.activity capability (super_admin only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Service layer (overview + per-user)

**Files:**
- Create: `kpis/activity_service.py`
- Test: `kpis/tests.py` (append `ActivityServiceTests`)

**Interfaces:**
- Consumes: `ACTIVITY_METRICS`, `MODULE_ORDER`, `headline_metrics` from `kpis.activity`; `period_options` from `kpis.periods`.
- Produces:
  - `activity_window(period) -> (date|None, date|None)` — `'all'` → `(None, None)`; otherwise `period_bounds(period)`.
  - `activity_period_options() -> list[(value, label)]` — `[('all', 'All time')] + period_options()`.
  - `build_activity_overview(period, sort='total') -> dict` with keys `period`, `period_label`, `headline_metrics` (list of `ActivityMetric`), `rows` (list of `{user, total, headline (dict key->count), counts (dict)}`), sorted by `sort` desc (`sort` is `'total'` or a headline metric key).
  - `build_user_activity(period, user) -> dict` with keys `period`, `period_label`, `user`, `total`, `modules` (list of `{module, items:[{label,count}]}` in `MODULE_ORDER`).

- [ ] **Step 1: Write the failing test**

Append to `kpis/tests.py`:

```python
class ActivityServiceTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name=Role.SALES_REP)
        self.active1 = User.objects.create_user('a1', password='pw', role=self.role)
        self.active2 = User.objects.create_user('a2', password='pw', role=self.role)
        self.inactive = User.objects.create_user('z', password='pw', role=self.role, is_active=False)
        self.region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        self.status = ProjectStatus.objects.create(name='Open', category='active')
        Project.objects.create(project_name='P1', proposal_reference='P1',
                               status=self.status, region=self.region, created_by=self.active1)
        Project.objects.create(project_name='P2', proposal_reference='P2',
                               status=self.status, region=self.region, created_by=self.active1)

    def test_overview_all_active_users_with_totals(self):
        from kpis.activity_service import build_activity_overview
        data = build_activity_overview('all')
        ids = [r['user'].id for r in data['rows']]
        self.assertIn(self.active1.id, ids)
        self.assertIn(self.active2.id, ids)
        self.assertNotIn(self.inactive.id, ids)        # inactive excluded
        top = data['rows'][0]                           # sorted by total desc
        self.assertEqual(top['user'].id, self.active1.id)
        self.assertEqual(top['total'], 2)
        self.assertEqual(top['headline']['projects_created'], 2)

    def test_user_detail_grouped_by_module(self):
        from kpis.activity_service import build_user_activity
        data = build_user_activity('all', self.active1)
        self.assertEqual(data['total'], 2)
        modules = [m['module'] for m in data['modules']]
        self.assertEqual(modules, ['Pipeline', 'Costing', 'Procurement', 'Proposals', 'Dev Tracking'])
        pipeline = next(m for m in data['modules'] if m['module'] == 'Pipeline')
        created = next(i for i in pipeline['items'] if i['label'] == 'Pipelines created')
        self.assertEqual(created['count'], 2)

    def test_activity_window_all_time(self):
        from kpis.activity_service import activity_window
        self.assertEqual(activity_window('all'), (None, None))
        self.assertEqual(activity_window('2026-Q2'),
                         (datetime.date(2026, 4, 1), datetime.date(2026, 7, 1)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test kpis.tests.ActivityServiceTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'kpis.activity_service'`.

- [ ] **Step 3: Write `kpis/activity_service.py`**

```python
"""Assemble the Team Activity overview + per-user breakdown from the activity
registry. One grouped query per metric for the overview (not per-user)."""
from django.contrib.auth import get_user_model

from .activity import ACTIVITY_METRICS, MODULE_ORDER, headline_metrics
from .periods import period_bounds, label_for, period_options


def activity_window(period):
    if period == 'all':
        return None, None
    return period_bounds(period)


def activity_period_options():
    return [('all', 'All time')] + period_options()


def _period_label(period):
    return 'All time' if period == 'all' else label_for(period)


def build_activity_overview(period, sort='total'):
    start, end = activity_window(period)
    metric_counts = {m.key: m.counts(start, end) for m in ACTIVITY_METRICS}
    heads = headline_metrics()
    head_keys = {m.key for m in heads}

    User = get_user_model()
    users = (User.objects.filter(is_active=True)
             .select_related('role')
             .order_by('first_name', 'last_name', 'username'))

    rows = []
    for u in users:
        counts = {m.key: metric_counts[m.key].get(u.id, 0) for m in ACTIVITY_METRICS}
        rows.append({
            'user': u,
            'total': sum(counts.values()),
            'headline': {k: counts[k] for k in head_keys},
            'counts': counts,
        })

    if sort != 'total' and sort in head_keys:
        rows.sort(key=lambda r: r['headline'][sort], reverse=True)
    else:
        rows.sort(key=lambda r: r['total'], reverse=True)

    return {
        'period': period,
        'period_label': _period_label(period),
        'headline_metrics': heads,
        'rows': rows,
        'sort': sort if (sort == 'total' or sort in head_keys) else 'total',
    }


def build_user_activity(period, user):
    start, end = activity_window(period)
    modules = []
    total = 0
    for module in MODULE_ORDER:
        items = []
        for m in ACTIVITY_METRICS:
            if m.module != module:
                continue
            c = m.count_for(start, end, user.id)
            total += c
            items.append({'label': m.label, 'count': c})
        modules.append({'module': module, 'items': items})
    return {
        'period': period,
        'period_label': _period_label(period),
        'user': user,
        'total': total,
        'modules': modules,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test kpis.tests.ActivityServiceTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add kpis/activity_service.py kpis/tests.py
git commit -m "kpis: activity overview + per-user service

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Views + URLs

**Files:**
- Modify: `kpis/views.py` (add two views + import)
- Modify: `kpis/urls.py` (add two routes)
- Test: `kpis/tests.py` (append `ActivityViewTests`)

**Interfaces:**
- Consumes: `build_activity_overview`, `build_user_activity`, `activity_period_options` from `kpis.activity_service`.
- Produces: URL names `kpis:activity` (`/kpis/activity/`) and `kpis:activity_detail` (`/kpis/activity/<int:user_id>/`), both gated by `@require_capability('kpis.activity')`.

- [ ] **Step 1: Write the failing test**

Append to `kpis/tests.py`:

```python
class ActivityViewTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _user(self, role_name):
        return User.objects.create_user(
            username=f'av_{role_name}', password='pw',
            role=Role.objects.get(name=role_name))

    def test_super_admin_sees_overview(self):
        self.client.force_login(self._user(Role.SUPER_ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:activity')).status_code, 200)

    def test_admin_denied_overview(self):
        self.client.force_login(self._user(Role.ADMIN))
        self.assertEqual(self.client.get(reverse('kpis:activity')).status_code, 403)

    def test_super_admin_sees_detail(self):
        admin = self._user(Role.SUPER_ADMIN)
        self.client.force_login(admin)
        url = reverse('kpis:activity_detail', args=[admin.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test kpis.tests.ActivityViewTests -v 2`
Expected: FAIL — `NoReverseMatch: 'activity' is not a valid view function or pattern name`.

- [ ] **Step 3: Add the views**

In `kpis/views.py`, add to the imports near the other service imports:

```python
from django.shortcuts import get_object_or_404
from django.contrib.auth import get_user_model
from .activity_service import (
    build_activity_overview, build_user_activity, activity_period_options,
)
```

(If `get_object_or_404` / `get_user_model` are already imported, don't duplicate.)

Add these two views at the end of `kpis/views.py`:

```python
def _resolve_activity_period(request):
    """'all' or a valid period string; defaults to 'all' (lifetime review)."""
    period = request.GET.get('period') or 'all'
    if period == 'all':
        return 'all'
    try:
        period_bounds(period)
        return period
    except (ValueError, TypeError):
        return 'all'


@login_required
@require_capability('kpis.activity')
def activity_overview(request):
    period = _resolve_activity_period(request)
    sort = request.GET.get('sort', 'total')
    data = build_activity_overview(period, sort=sort)
    return render(request, 'kpis/activity_overview.html', {
        'data': data,
        'period': period,
        'period_options': activity_period_options(),
    })


@login_required
@require_capability('kpis.activity')
def activity_detail(request, user_id):
    period = _resolve_activity_period(request)
    user = get_object_or_404(get_user_model(), pk=user_id)
    data = build_user_activity(period, user)
    return render(request, 'kpis/activity_detail.html', {
        'data': data,
        'period': period,
        'period_options': activity_period_options(),
    })
```

- [ ] **Step 4: Add the routes**

In `kpis/urls.py`, add to `urlpatterns`:

```python
    path('activity/', views.activity_overview, name='activity'),
    path('activity/<int:user_id>/', views.activity_detail, name='activity_detail'),
```

- [ ] **Step 5: Create minimal templates so the views render**

Create `kpis/templates/kpis/activity_overview.html`:

```django
{% extends 'base.html' %}
{% block title %}Team Activity — Leap Networks ERP{% endblock %}
{% block page_title %}Team Activity{% endblock %}
{% block content %}
<div class="container-fluid"><p>{{ data.period_label }} — {{ data.rows|length }} users</p></div>
{% endblock %}
```

Create `kpis/templates/kpis/activity_detail.html`:

```django
{% extends 'base.html' %}
{% block title %}Activity — {{ data.user.username }}{% endblock %}
{% block page_title %}Activity{% endblock %}
{% block content %}
<div class="container-fluid"><p>{{ data.user.get_full_name|default:data.user.username }} — {{ data.total }} actions</p></div>
{% endblock %}
```

(Full templates are built in Task 5; these stubs let the view tests pass first.)

- [ ] **Step 6: Run test to verify it passes**

Run: `python manage.py test kpis.tests.ActivityViewTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add kpis/views.py kpis/urls.py kpis/templates/kpis/activity_overview.html kpis/templates/kpis/activity_detail.html kpis/tests.py
git commit -m "kpis: activity overview + detail views (super_admin gated)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Full templates + nav link

**Files:**
- Modify: `kpis/templates/kpis/activity_overview.html` (full table)
- Modify: `kpis/templates/kpis/activity_detail.html` (full breakdown)
- Modify: `templates/base.html` (nav link in the Department KPIs section)

**Interfaces:**
- Consumes: the `data` dict shapes from Task 3; `period_options`.

- [ ] **Step 1: Write the full overview template**

Replace the contents of `kpis/templates/kpis/activity_overview.html`:

```django
{% extends 'base.html' %}
{% block title %}Team Activity — Leap Networks ERP{% endblock %}
{% block page_title %}Team Activity{% endblock %}
{% block content %}
<div class="container-fluid">
    <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
        <div>
            <h1 class="h3 mb-1">Team Activity</h1>
            <p class="text-muted mb-0">How active each person is across the ERP — {{ data.period_label }}</p>
        </div>
        <form method="get" class="d-flex gap-2 align-items-center">
            <input type="hidden" name="sort" value="{{ data.sort }}">
            <select name="period" class="form-select form-select-sm" onchange="this.form.submit()" style="min-width:150px;">
                {% for val, lbl in period_options %}
                <option value="{{ val }}" {% if val == period %}selected{% endif %}>{{ lbl }}</option>
                {% endfor %}
            </select>
        </form>
    </div>

    <div class="card">
        <div class="card-body p-0">
            <div class="table-responsive">
                <table class="table table-hover align-middle mb-0">
                    <thead class="table-dark">
                        <tr>
                            <th>Person</th>
                            {% for m in data.headline_metrics %}
                            <th class="text-end"><a href="?period={{ period }}&sort={{ m.key }}" class="text-white text-decoration-none">{{ m.label }}</a></th>
                            {% endfor %}
                            <th class="text-end"><a href="?period={{ period }}&sort=total" class="text-white text-decoration-none">Total</a></th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for row in data.rows %}
                        <tr>
                            <td>
                                <a href="{% url 'kpis:activity_detail' row.user.pk %}?period={{ period }}" class="text-decoration-none">
                                    {{ row.user.get_full_name|default:row.user.username }}
                                </a>
                                {% if row.user.role %}<span class="text-muted small d-block">{{ row.user.role.get_name_display }}</span>{% endif %}
                            </td>
                            {% for m in data.headline_metrics %}
                            <td class="text-end">{{ row.headline|get_item:m.key }}</td>
                            {% endfor %}
                            <td class="text-end fw-bold">{{ row.total }}</td>
                        </tr>
                        {% empty %}
                        <tr><td colspan="9" class="text-center text-muted py-5">No users.</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
{% endblock %}
```

NOTE: the `get_item` filter (dict lookup by variable key) does not exist by default. Use a tiny inline approach instead — replace the headline `<td>` loop with explicit access via a helper. To avoid a custom template tag, change the service to also attach an ordered list. Implement this in Step 2.

- [ ] **Step 2: Make headline values iterable in order (avoid a custom filter)**

In `kpis/activity_service.py`, inside `build_activity_overview`, change the per-row dict to include an ordered `headline_cells` list aligned with `heads`:

```python
    rows = []
    for u in users:
        counts = {m.key: metric_counts[m.key].get(u.id, 0) for m in ACTIVITY_METRICS}
        rows.append({
            'user': u,
            'total': sum(counts.values()),
            'headline': {k: counts[k] for k in head_keys},
            'headline_cells': [counts[m.key] for m in heads],
            'counts': counts,
        })
```

Then in the overview template replace the headline `<td>` loop:

```django
                            {% for m in data.headline_metrics %}
                            <td class="text-end">{{ row.headline|get_item:m.key }}</td>
                            {% endfor %}
```

with:

```django
                            {% for cell in row.headline_cells %}
                            <td class="text-end">{{ cell }}</td>
                            {% endfor %}
```

- [ ] **Step 3: Write the full detail template**

Replace the contents of `kpis/templates/kpis/activity_detail.html`:

```django
{% extends 'base.html' %}
{% block title %}Activity — {{ data.user.get_full_name|default:data.user.username }}{% endblock %}
{% block page_title %}Activity{% endblock %}
{% block content %}
<div class="container-fluid">
    <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap gap-2">
        <div class="d-flex align-items-center gap-2">
            <i class="bi bi-person-circle fs-2 text-primary"></i>
            <div>
                <h1 class="h4 mb-0">{{ data.user.get_full_name|default:data.user.username }}</h1>
                <span class="text-muted small">{% if data.user.role %}{{ data.user.role.get_name_display }} · {% endif %}{{ data.total }} actions · {{ data.period_label }}</span>
            </div>
        </div>
        <div class="d-flex gap-2 align-items-center">
            <form method="get" class="d-flex gap-2 align-items-center">
                <select name="period" class="form-select form-select-sm" onchange="this.form.submit()" style="min-width:150px;">
                    {% for val, lbl in period_options %}
                    <option value="{{ val }}" {% if val == period %}selected{% endif %}>{{ lbl }}</option>
                    {% endfor %}
                </select>
            </form>
            <a href="{% url 'kpis:activity' %}?period={{ period }}" class="btn btn-sm btn-outline-secondary"><i class="bi bi-table"></i> All users</a>
        </div>
    </div>

    <div class="row g-3">
        {% for mod in data.modules %}
        <div class="col-md-6 col-xl-4">
            <div class="card h-100">
                <div class="card-header bg-white"><strong>{{ mod.module }}</strong></div>
                <ul class="list-group list-group-flush">
                    {% for item in mod.items %}
                    <li class="list-group-item d-flex justify-content-between">
                        <span>{{ item.label }}</span>
                        <span class="badge {% if item.count %}bg-primary{% else %}bg-light text-muted border{% endif %}">{{ item.count }}</span>
                    </li>
                    {% endfor %}
                </ul>
            </div>
        </div>
        {% endfor %}
    </div>

    <p class="text-muted small mt-3">
        <i class="bi bi-info-circle"></i> Performance score (combining these into a weighted figure to attach to KPIs) is coming soon.
    </p>
</div>
{% endblock %}
```

- [ ] **Step 4: Add the nav link**

In `templates/base.html`, find the Department KPIs nav block. After the "By Person" `<li>` (the link whose `url_name == 'people'`) and before the `{% if user|can:'kpis.manage' %}` line, insert:

```django
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.app_name == 'kpis' and 'activity' in request.resolver_match.url_name %}active{% endif %}" href="{% url 'kpis:activity' %}" data-title="Team Activity">
                    <i class="bi bi-activity"></i> <span>Team Activity</span>
                </a>
            </li>
```

Then gate it: wrap that `<li>` in `{% if user|can:'kpis.activity' %} … {% endif %}` so it shows only to holders of the new capability:

```django
            {% if user|can:'kpis.activity' %}
            <li class="nav-item">
                <a class="nav-link {% if request.resolver_match.app_name == 'kpis' and 'activity' in request.resolver_match.url_name %}active{% endif %}" href="{% url 'kpis:activity' %}" data-title="Team Activity">
                    <i class="bi bi-activity"></i> <span>Team Activity</span>
                </a>
            </li>
            {% endif %}
```

- [ ] **Step 5: Verify the rendered overview shows headline columns + a total**

Append to `kpis/tests.py` (in `ActivityViewTests`):

```python
    def test_overview_renders_user_and_total(self):
        from projects.models import Region, ProjectStatus, Project
        admin = self._user(Role.SUPER_ADMIN)
        region = Region.objects.create(name='KSA', code='LNA', currency='SAR')
        status = ProjectStatus.objects.create(name='Open', category='active')
        Project.objects.create(project_name='P1', proposal_reference='P1',
                               status=status, region=region, created_by=admin)
        self.client.force_login(admin)
        resp = self.client.get(reverse('kpis:activity'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pipelines created')
        self.assertContains(resp, 'Total')
```

- [ ] **Step 6: Run the full kpis suite**

Run: `python manage.py test kpis -v 1`
Expected: PASS (all kpis tests, including the new activity tests).

- [ ] **Step 7: Run check + full suite**

Run: `python manage.py check && python manage.py test`
Expected: `System check identified no issues`; suite `OK`.

- [ ] **Step 8: Commit**

```bash
git add kpis/templates/kpis/activity_overview.html kpis/templates/kpis/activity_detail.html kpis/activity_service.py templates/base.html kpis/tests.py
git commit -m "kpis: Team Activity overview table + per-user detail + nav

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review notes

- **Spec coverage:** registry (Task 1) ✓; capability + migration (Task 2) ✓; overview + detail service incl. all-active-users & totals (Task 3) ✓; views + gating (Task 4) ✓; table/detail/nav presentation (Task 5) ✓. Period selector + all-time via `activity_period_options` ✓. "Attach to KPIs later" placeholder in detail template ✓.
- **Headline metrics** match the spec's 7 (Pipelines, BOMs, Sales finalised, To finance, POs, Proposals, Tasks done).
- **Type consistency:** `counts(start,end)->dict`, `count_for(start,end,user_id)->int`, service dict keys (`rows`, `headline_metrics`, `headline_cells`, `modules`) are used consistently across Tasks 3–5.
- **DevTask caveat:** `tasks_created` was dropped (no creator field); `stacks_created` (TaskStack.created_by) + `tasks_completed` (developer/completed_at) used instead — 21 metrics total.
