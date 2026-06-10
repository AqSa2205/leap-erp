# Permission Matrix UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give super admins a UI to toggle action-level permissions per role, so access control becomes editable data instead of hardcoded role checks.

**Architecture:** Capabilities are declared in a code registry (`accounts/permissions.py`); grants live in a `RolePermission` DB table edited via a super-admin-only grid. `User.has_capability(codename)` is the single enforcement API (super_admin always passes). Phase 1 enforces only module **access** + **nav** capabilities; region/workflow-stage/ownership scoping stays in code, layered on top. A seed migration reproduces today's behavior, plus the one deliberate change: finance gains the region-scoped pipeline view.

**Tech Stack:** Django 6.0, PostgreSQL, Django test runner (`python manage.py test`), ReportLab-free (pure server + Bootstrap templates), vanilla-JS AJAX (matching existing `costing_detail.html` inline-edit pattern).

**Spec:** `docs/superpowers/specs/2026-06-10-permission-matrix-ui-design.md`

**Test command convention:** `python manage.py test accounts -v 2` (run a single test: `python manage.py test accounts.tests.HasCapabilityTests.test_super_admin_always_true -v 2`). All new tests live in `accounts/tests.py` unless noted.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `accounts/permissions.py` | Capability dataclass, `CAPABILITIES` registry, `DEFAULT_GRANTS`, `seed_default_permissions()`, `require_capability`, `CapabilityRequiredMixin` | Create |
| `accounts/models.py` | `RolePermission`, `PermissionChangeLog` models; `User.has_capability()` | Modify |
| `accounts/migrations/000X_rolepermission.py` | Schema for the two new models | Create (auto) |
| `accounts/migrations/000Y_seed_permissions.py` | Data migration calling `seed_default_permissions()` | Create |
| `accounts/templatetags/__init__.py` | Make templatetags a package | Create |
| `accounts/templatetags/perms.py` | `can` template filter | Create |
| `accounts/views.py` | `permission_matrix` view + `ajax_toggle_permission` endpoint | Modify |
| `accounts/urls.py` | Routes for the two views | Modify |
| `templates/accounts/permission_matrix.html` | The super-admin grid + AJAX JS | Create |
| `dashboard/views.py` | Capability gate + finance region branch on `index` | Modify |
| `projects/views.py` | Capability gate + finance region branch in `ProjectPermissionMixin` | Modify |
| `costing/views.py` | `costing.access` gate on `CostingListView` | Modify |
| `procurement/views.py` | `*.access` gates on `procurement_dashboard`, `POListView`, `DNListView` | Modify |
| `templates/base.html` | `\|can` nav gating + Settings → Permissions link | Modify |
| `accounts/tests.py` | All tests for the above | Modify |

---

## Task 1: Capability registry

**Files:**
- Create: `accounts/permissions.py`
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
from django.test import TestCase
from accounts.permissions import CAPABILITIES, Capability, capability_codenames


class RegistryTests(TestCase):
    def test_codenames_are_unique(self):
        codes = [c.codename for c in CAPABILITIES]
        self.assertEqual(len(codes), len(set(codes)), "duplicate capability codenames")

    def test_every_capability_has_module_and_action(self):
        for c in CAPABILITIES:
            self.assertTrue(c.module, c.codename)
            self.assertTrue(c.action, c.codename)

    def test_access_and_nav_exist_for_each_main_module(self):
        for module_key in ['dashboard', 'pipeline', 'costing', 'procurement', 'po', 'dn', 'settings']:
            self.assertIn(f'{module_key}.access', capability_codenames())
            self.assertIn(f'{module_key}.nav', capability_codenames())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.RegistryTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'accounts.permissions'`

- [ ] **Step 3: Write minimal implementation**

Create `accounts/permissions.py`:

```python
"""Capability registry and enforcement helpers.

Capabilities are declared HERE in code (a capability only means something if a
code path checks it). Only the per-role on/off *grants* live in the database
(`accounts.models.RolePermission`). The super-admin grid renders this registry
crossed with the roles.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    codename: str   # 'costing.access'
    module: str     # display group, e.g. 'Costing'
    action: str     # 'access' | 'nav' | 'view' | 'create' | 'edit' | 'delete' | 'export' | 'approve'
    label: str      # human label for the grid cell/row
    enforced: bool  # True = a code path reads this today; False = defined, wiring pending
    order: int = 0


def _module(key, label, *, granular=()):
    """Build access + nav (enforced) plus optional granular (not-yet-enforced) caps."""
    caps = [
        Capability(f'{key}.access', label, 'access', f'Open {label}', enforced=True, order=0),
        Capability(f'{key}.nav', label, 'nav', f'Show {label} in sidebar', enforced=True, order=1),
    ]
    for i, (action, lbl) in enumerate(granular, start=2):
        caps.append(Capability(f'{key}.{action}', label, action, lbl, enforced=False, order=i))
    return caps


CAPABILITIES = [
    *_module('dashboard', 'Dashboard'),
    *_module('pipeline', 'Commercial Pipeline'),
    *_module('costing', 'Costing', granular=[
        ('view', 'View pricing'), ('create', 'Create sheets'), ('edit', 'Edit sheets'),
        ('delete', 'Delete sheets/items'), ('export', 'Export PDF'), ('approve', 'Approve / release'),
    ]),
    *_module('procurement', 'Procurement'),
    *_module('po', 'Purchase Orders', granular=[
        ('create', 'Create PO'), ('edit', 'Edit PO'), ('delete', 'Delete PO'),
        ('export', 'Export PO'), ('approve', 'Approve PO'),
    ]),
    *_module('dn', 'Delivery Notes', granular=[
        ('create', 'Create DN'), ('edit', 'Edit DN'), ('delete', 'Delete DN'), ('export', 'Export DN'),
    ]),
    *_module('settings', 'Admin / Settings'),
]


def capability_codenames():
    return {c.codename for c in CAPABILITIES}


def capabilities_by_module():
    """Ordered {module_label: [Capability, ...]} for rendering the grid."""
    out = {}
    for c in CAPABILITIES:
        out.setdefault(c.module, []).append(c)
    for caps in out.values():
        caps.sort(key=lambda c: c.order)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test accounts.tests.RegistryTests -v 2`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add accounts/permissions.py accounts/tests.py
git commit -m "Permissions: capability registry"
```

---

## Task 2: RolePermission + PermissionChangeLog models

**Files:**
- Modify: `accounts/models.py` (append after the `User` model)
- Create: `accounts/migrations/000X_rolepermission.py` (via makemigrations)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
from django.db import IntegrityError
from accounts.models import Role, RolePermission, PermissionChangeLog, User


class RolePermissionModelTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name=Role.FINANCE_REP)

    def test_create_grant(self):
        g = RolePermission.objects.create(role=self.role, codename='costing.access', allowed=True)
        self.assertTrue(g.allowed)

    def test_role_codename_is_unique(self):
        RolePermission.objects.create(role=self.role, codename='costing.access', allowed=True)
        with self.assertRaises(IntegrityError):
            RolePermission.objects.create(role=self.role, codename='costing.access', allowed=False)

    def test_change_log_records(self):
        u = User.objects.create_user('toggler', password='x')
        PermissionChangeLog.objects.create(actor=u, role=self.role, codename='po.access', allowed=True)
        self.assertEqual(PermissionChangeLog.objects.count(), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.RolePermissionModelTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'RolePermission'`

- [ ] **Step 3: Write minimal implementation**

Append to `accounts/models.py` (end of file):

```python
class RolePermission(models.Model):
    """Per-role on/off grant for a capability codename (see accounts.permissions)."""
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='permissions')
    codename = models.CharField(max_length=64)
    allowed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('role', 'codename')
        indexes = [models.Index(fields=['role', 'codename'])]

    def __str__(self):
        return f"{self.role.name}:{self.codename}={'on' if self.allowed else 'off'}"


class PermissionChangeLog(models.Model):
    """Audit trail for grant toggles (security-sensitive)."""
    actor = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, related_name='+')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='+')
    codename = models.CharField(max_length=64)
    allowed = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
```

- [ ] **Step 4: Create the schema migration**

Run: `python manage.py makemigrations accounts`
Expected: creates `accounts/migrations/000X_rolepermission.py` adding both models.

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test accounts.tests.RolePermissionModelTests -v 2`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add accounts/models.py accounts/migrations/
git commit -m "Permissions: RolePermission + PermissionChangeLog models"
```

---

## Task 3: User.has_capability() with per-request cache

**Files:**
- Modify: `accounts/models.py` (add method to `User`)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
class HasCapabilityTests(TestCase):
    def setUp(self):
        self.fin = Role.objects.create(name=Role.FINANCE_REP)
        self.sa = Role.objects.create(name=Role.SUPER_ADMIN)
        self.fin_user = User.objects.create_user('fin', password='x')
        self.fin_user.role = self.fin
        self.fin_user.save()
        self.sa_user = User.objects.create_user('sa', password='x')
        self.sa_user.role = self.sa
        self.sa_user.save()
        RolePermission.objects.create(role=self.fin, codename='costing.access', allowed=True)
        RolePermission.objects.create(role=self.fin, codename='po.access', allowed=False)

    def test_granted_capability_true(self):
        self.assertTrue(self.fin_user.has_capability('costing.access'))

    def test_denied_capability_false(self):
        self.assertFalse(self.fin_user.has_capability('po.access'))

    def test_missing_grant_defaults_false(self):
        self.assertFalse(self.fin_user.has_capability('dn.access'))

    def test_super_admin_always_true(self):
        self.assertTrue(self.sa_user.has_capability('anything.at.all'))

    def test_user_without_role_false(self):
        roleless = User.objects.create_user('none', password='x')
        self.assertFalse(roleless.has_capability('costing.access'))

    def test_cache_is_consistent_within_instance(self):
        first = self.fin_user.has_capability('costing.access')
        RolePermission.objects.filter(role=self.fin, codename='costing.access').update(allowed=False)
        # Same in-memory user keeps its cached snapshot until reloaded.
        self.assertEqual(self.fin_user.has_capability('costing.access'), first)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.HasCapabilityTests -v 2`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'has_capability'`

- [ ] **Step 3: Write minimal implementation**

In `accounts/models.py`, add this method to the `User` class (place near the other `is_*_user` helpers):

```python
    def has_capability(self, codename):
        """Single enforcement API. super_admin always passes (lockout safety).

        Caches the role's allowed codenames on the instance so nav rendering
        does not fan out queries. Cache lives for the life of this Python
        object (i.e. one request via `request.user`).
        """
        if not self.is_authenticated:
            return False
        if self.is_super_admin_user:
            return True
        if self.role_id is None:
            return False
        cache = getattr(self, '_capability_cache', None)
        if cache is None:
            from accounts.models import RolePermission  # local import: same module, runtime-safe
            cache = set(
                RolePermission.objects
                .filter(role_id=self.role_id, allowed=True)
                .values_list('codename', flat=True)
            )
            self._capability_cache = cache
        return codename in cache
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test accounts.tests.HasCapabilityTests -v 2`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add accounts/models.py accounts/tests.py
git commit -m "Permissions: User.has_capability with per-request cache"
```

---

## Task 4: require_capability decorator + CapabilityRequiredMixin

**Files:**
- Modify: `accounts/permissions.py` (append)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
from django.test import RequestFactory
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from accounts.permissions import require_capability, CapabilityRequiredMixin


class GateTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.role = Role.objects.create(name=Role.FINANCE_REP)
        self.user = User.objects.create_user('u', password='x')
        self.user.role = self.role
        self.user.save()
        RolePermission.objects.create(role=self.role, codename='costing.access', allowed=True)

    def _req(self, user):
        r = self.rf.get('/x/')
        r.user = user
        return r

    def test_decorator_allows_when_granted(self):
        @require_capability('costing.access')
        def view(request):
            return HttpResponse('ok')
        self.assertEqual(view(self._req(self.user)).status_code, 200)

    def test_decorator_blocks_when_missing(self):
        @require_capability('po.access')
        def view(request):
            return HttpResponse('ok')
        with self.assertRaises(PermissionDenied):
            view(self._req(self.user))

    def test_mixin_blocks_when_missing(self):
        from django.views import View

        class V(CapabilityRequiredMixin, View):
            capability = 'po.access'
            def get(self, request):
                return HttpResponse('ok')

        with self.assertRaises(PermissionDenied):
            V.as_view()(self._req(self.user))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.GateTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'require_capability'`

- [ ] **Step 3: Write minimal implementation**

Append to `accounts/permissions.py`:

```python
from functools import wraps
from django.core.exceptions import PermissionDenied


def require_capability(codename):
    """Decorator for function views: 403 unless the user holds `codename`."""
    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            user = getattr(request, 'user', None)
            if user is None or not user.is_authenticated or not user.has_capability(codename):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return _wrapped
    return decorator


class CapabilityRequiredMixin:
    """Class-based-view mixin. Set `capability = '<codename>'`."""
    capability = None

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated or not user.has_capability(self.capability):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test accounts.tests.GateTests -v 2`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add accounts/permissions.py accounts/tests.py
git commit -m "Permissions: require_capability decorator + mixin"
```

---

## Task 5: `can` template filter

**Files:**
- Create: `accounts/templatetags/__init__.py` (empty)
- Create: `accounts/templatetags/perms.py`
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
from django.template import Template, Context


class TemplateFilterTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name=Role.FINANCE_REP)
        self.user = User.objects.create_user('u', password='x')
        self.user.role = self.role
        self.user.save()
        RolePermission.objects.create(role=self.role, codename='costing.access', allowed=True)

    def _render(self, codename, user):
        t = Template("{% load perms %}{% if user|can:cap %}YES{% else %}NO{% endif %}")
        return t.render(Context({'user': user, 'cap': codename}))

    def test_filter_true(self):
        self.assertEqual(self._render('costing.access', self.user), 'YES')

    def test_filter_false(self):
        self.assertEqual(self._render('po.access', self.user), 'NO')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.TemplateFilterTests -v 2`
Expected: FAIL — `TemplateSyntaxError: 'perms' is not a registered tag library`

- [ ] **Step 3: Write minimal implementation**

Create `accounts/templatetags/__init__.py` (empty file).

Create `accounts/templatetags/perms.py`:

```python
from django import template

register = template.Library()


@register.filter(name='can')
def can(user, codename):
    """Usage: {% if user|can:'costing.access' %}"""
    return bool(user and getattr(user, 'is_authenticated', False) and user.has_capability(codename))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test accounts.tests.TemplateFilterTests -v 2`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add accounts/templatetags/ accounts/tests.py
git commit -m "Permissions: can template filter"
```

---

## Task 6: Seed defaults (function + data migration)

**Goal:** Reproduce current behavior for every (role × capability), plus the one deliberate change — finance gets `pipeline.access`/`pipeline.nav` ON.

**Files:**
- Modify: `accounts/permissions.py` (append `DEFAULT_GRANTS` + `seed_default_permissions`)
- Create: `accounts/migrations/000Y_seed_permissions.py`
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
from accounts.permissions import seed_default_permissions


class SeedTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()

    def _allowed(self, role_name, codename):
        role = Role.objects.get(name=role_name)
        return RolePermission.objects.get(role=role, codename=codename).allowed

    def test_every_role_capability_pair_has_a_row(self):
        from accounts.permissions import capability_codenames
        n_roles = Role.objects.count()
        n_caps = len(capability_codenames())
        self.assertEqual(RolePermission.objects.count(), n_roles * n_caps)

    def test_finance_gets_pipeline_access(self):
        self.assertTrue(self._allowed(Role.FINANCE_REP, 'pipeline.access'))
        self.assertTrue(self._allowed(Role.FINANCE_REP, 'pipeline.nav'))

    def test_finance_no_procurement(self):
        self.assertFalse(self._allowed(Role.FINANCE_REP, 'procurement.access'))

    def test_sales_rep_pipeline_and_costing(self):
        self.assertTrue(self._allowed(Role.SALES_REP, 'pipeline.access'))
        self.assertTrue(self._allowed(Role.SALES_REP, 'costing.access'))

    def test_procurement_gets_po_dn(self):
        self.assertTrue(self._allowed(Role.PROCUREMENT_OFF, 'po.access'))
        self.assertTrue(self._allowed(Role.PROCUREMENT_OFF, 'dn.access'))

    def test_only_super_admin_gets_settings(self):
        self.assertTrue(self._allowed(Role.SUPER_ADMIN, 'settings.access'))
        self.assertFalse(self._allowed(Role.SALES_REP, 'settings.access'))

    def test_seed_is_idempotent(self):
        before = RolePermission.objects.count()
        seed_default_permissions()
        self.assertEqual(RolePermission.objects.count(), before)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.SeedTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'seed_default_permissions'`

- [ ] **Step 3: Write minimal implementation**

Append to `accounts/permissions.py`:

```python
# Module access baseline = today's behavior. Keys are role.name. The value is
# the set of MODULE keys whose `.access` + `.nav` are ON. Granular caps
# (enforced=False) are seeded OFF for everyone for now and toggled on later.
DEFAULT_MODULE_ACCESS = {
    'super_admin':     {'dashboard', 'pipeline', 'costing', 'procurement', 'po', 'dn', 'settings'},
    'admin':           {'dashboard', 'pipeline', 'costing'},
    'manager':         {'dashboard', 'pipeline', 'costing'},
    'sales_rep':       {'dashboard', 'pipeline', 'costing'},
    'procurement_mgr': {'dashboard', 'costing', 'procurement', 'po', 'dn'},
    'procurement_off': {'dashboard', 'costing', 'procurement', 'po', 'dn'},
    'proposal_head':   {'dashboard', 'pipeline', 'costing'},
    'proposal_rep':    {'dashboard', 'pipeline', 'costing'},
    # Deliberate launch change: finance gains the (region-scoped) pipeline view.
    'finance_head':    {'dashboard', 'pipeline', 'costing'},
    'finance_manager': {'dashboard', 'pipeline', 'costing'},
    'finance_rep':     {'dashboard', 'pipeline', 'costing'},
}


def seed_default_permissions():
    """Create a RolePermission row for every (role x capability), set to the
    baseline above. Idempotent: existing rows are left as the admin set them;
    only missing rows are created. Safe to call from a data migration and tests.
    """
    from accounts.models import Role, RolePermission
    access_actions = {'access', 'nav'}
    for role in Role.objects.all():
        modules_on = DEFAULT_MODULE_ACCESS.get(role.name, set())
        for cap in CAPABILITIES:
            module_key = cap.codename.rsplit('.', 1)[0]
            default_allowed = cap.action in access_actions and module_key in modules_on
            RolePermission.objects.get_or_create(
                role=role, codename=cap.codename,
                defaults={'allowed': default_allowed},
            )
```

> Note: `seed_default_permissions` derives `module_key` as the codename minus its action suffix (`costing.access` → `costing`), matching `DEFAULT_MODULE_ACCESS` keys.

- [ ] **Step 4: Run the function test**

Run: `python manage.py test accounts.tests.SeedTests -v 2`
Expected: PASS (7 tests)

- [ ] **Step 5: Create the data migration**

Run: `python manage.py makemigrations accounts --empty --name seed_permissions`
Then edit the created `accounts/migrations/000Y_seed_permissions.py` to:

```python
from django.db import migrations


def seed(apps, schema_editor):
    # Import the plain function; it uses accounts.models at runtime which is
    # fine for a one-shot data migration (no historical-model fields needed).
    from accounts.permissions import seed_default_permissions
    seed_default_permissions()


def unseed(apps, schema_editor):
    RolePermission = apps.get_model('accounts', 'RolePermission')
    RolePermission.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '000X_rolepermission'),  # replace with the actual Task 2 migration name
    ]
    operations = [migrations.RunPython(seed, unseed)]
```

- [ ] **Step 6: Apply migrations**

Run: `python manage.py migrate accounts`
Expected: both migrations apply; no errors.

- [ ] **Step 7: Commit**

```bash
git add accounts/permissions.py accounts/migrations/ accounts/tests.py
git commit -m "Permissions: seed defaults (finance gains pipeline) + data migration"
```

---

## Task 7: Super-admin permission grid (view + URL + template, GET only)

**Files:**
- Modify: `accounts/views.py` (add `permission_matrix`)
- Modify: `accounts/urls.py` (add route)
- Create: `templates/accounts/permission_matrix.html`
- Modify: `templates/base.html` (add Settings → Permissions link)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
from django.urls import reverse


class PermissionMatrixViewTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.sa = User.objects.create_user('sa', password='x')
        self.sa.role = Role.objects.get(name=Role.SUPER_ADMIN)
        self.sa.save()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()

    def test_super_admin_can_open(self):
        self.client.force_login(self.sa)
        resp = self.client.get(reverse('accounts:permission_matrix'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Commercial Pipeline')

    def test_non_super_admin_forbidden(self):
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('accounts:permission_matrix'))
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.PermissionMatrixViewTests -v 2`
Expected: FAIL — `NoReverseMatch: 'permission_matrix' not found`

- [ ] **Step 3: Implement the view**

In `accounts/views.py`, add (keep imports tidy at top):

```python
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from accounts.permissions import capabilities_by_module
from accounts.models import Role, RolePermission


@login_required
def permission_matrix(request):
    """Super-admin-only grid of role x capability toggles.

    Hardcoded super_admin gate (NOT capability-gated) so the page can never be
    toggled away or used to lock everyone out.
    """
    if not request.user.is_super_admin_user:
        raise PermissionDenied

    roles = list(Role.objects.all())
    # {(role_id, codename): allowed}
    grant_map = {
        (g.role_id, g.codename): g.allowed
        for g in RolePermission.objects.all()
    }
    modules = []
    for module_label, caps in capabilities_by_module().items():
        rows = []
        for cap in caps:
            cells = [{
                'role': role,
                'allowed': grant_map.get((role.id, cap.codename), False),
                'locked': role.name == Role.SUPER_ADMIN,  # super admin always on
            } for role in roles]
            rows.append({'cap': cap, 'cells': cells})
        modules.append({'label': module_label, 'rows': rows})

    return render(request, 'accounts/permission_matrix.html', {
        'roles': roles,
        'modules': modules,
    })
```

- [ ] **Step 4: Add the route**

In `accounts/urls.py`, add inside `urlpatterns`:

```python
    path('settings/permissions/', views.permission_matrix, name='permission_matrix'),
```

- [ ] **Step 5: Create the template**

Create `templates/accounts/permission_matrix.html`:

```html
{% extends 'base.html' %}
{% load perms %}
{% block content %}
<div class="container-fluid py-3">
  <h4 class="mb-1">Permissions</h4>
  <p class="text-muted small">Toggle what each role can do. Super Admin is always allowed.
     Cells marked <span class="badge bg-light text-muted">wiring pending</span> are saved but not yet enforced.</p>

  {% for module in modules %}
  <div class="card mb-3">
    <div class="card-header py-2 fw-semibold">{{ module.label }}</div>
    <div class="table-responsive">
      <table class="table table-sm table-bordered mb-0 align-middle">
        <thead>
          <tr>
            <th style="min-width:220px;">Capability</th>
            {% for role in roles %}<th class="text-center small">{{ role.get_name_display }}</th>{% endfor %}
          </tr>
        </thead>
        <tbody>
          {% for row in module.rows %}
          <tr>
            <td class="small">
              {{ row.cap.label }}
              {% if not row.cap.enforced %}<span class="badge bg-light text-muted ms-1" title="Defined in the grid but not yet enforced in code">wiring pending</span>{% endif %}
            </td>
            {% for cell in row.cells %}
            <td class="text-center">
              <input type="checkbox" class="perm-toggle"
                     data-role="{{ cell.role.id }}" data-codename="{{ row.cap.codename }}"
                     {% if cell.allowed %}checked{% endif %} {% if cell.locked %}checked disabled{% endif %}>
            </td>
            {% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endfor %}
</div>

<script>
// AJAX per-cell toggle — Task 8 wires the endpoint. Placeholder no-op until then.
</script>
{% endblock %}
```

- [ ] **Step 6: Add the sidebar link (super admin only)**

In `templates/base.html`, inside the existing `{% if user.is_super_admin_user %}` admin/settings area of the sidebar (search for the Settings/Users links), add:

```html
<li class="nav-item">
  <a class="nav-link {% if request.resolver_match.url_name == 'permission_matrix' %}active{% endif %}" href="{% url 'accounts:permission_matrix' %}" data-title="Permissions">
    <i class="bi bi-shield-lock"></i> <span>Permissions</span>
  </a>
</li>
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python manage.py test accounts.tests.PermissionMatrixViewTests -v 2`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add accounts/views.py accounts/urls.py templates/accounts/permission_matrix.html templates/base.html accounts/tests.py
git commit -m "Permissions: super-admin grid view (read-only)"
```

---

## Task 8: AJAX toggle endpoint + audit + JS wiring

**Files:**
- Modify: `accounts/views.py` (add `ajax_toggle_permission`)
- Modify: `accounts/urls.py` (add route)
- Modify: `templates/accounts/permission_matrix.html` (replace the `<script>` no-op)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
import json


class TogglePermissionTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.sa = User.objects.create_user('sa', password='x')
        self.sa.role = Role.objects.get(name=Role.SUPER_ADMIN)
        self.sa.save()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()
        self.fin_role = Role.objects.get(name=Role.FINANCE_REP)

    def _toggle(self, role_id, codename, allowed):
        return self.client.post(
            reverse('accounts:toggle_permission'),
            data=json.dumps({'role': role_id, 'codename': codename, 'allowed': allowed}),
            content_type='application/json',
        )

    def test_toggle_flips_grant_and_logs(self):
        self.client.force_login(self.sa)
        resp = self._toggle(self.fin_role.id, 'po.access', True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(RolePermission.objects.get(role=self.fin_role, codename='po.access').allowed)
        self.assertEqual(PermissionChangeLog.objects.filter(codename='po.access', allowed=True).count(), 1)

    def test_non_super_admin_forbidden(self):
        self.client.force_login(self.fin)
        resp = self._toggle(self.fin_role.id, 'po.access', True)
        self.assertEqual(resp.status_code, 403)

    def test_cannot_toggle_super_admin_row(self):
        self.client.force_login(self.sa)
        sa_role = Role.objects.get(name=Role.SUPER_ADMIN)
        resp = self._toggle(sa_role.id, 'po.access', False)
        self.assertEqual(resp.status_code, 400)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.TogglePermissionTests -v 2`
Expected: FAIL — `NoReverseMatch: 'toggle_permission' not found`

- [ ] **Step 3: Implement the endpoint**

In `accounts/views.py`, add:

```python
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from accounts.models import PermissionChangeLog


@login_required
@require_POST
def ajax_toggle_permission(request):
    if not request.user.is_super_admin_user:
        raise PermissionDenied
    try:
        payload = json.loads(request.body or '{}')
        role_id = int(payload['role'])
        codename = str(payload['codename'])
        allowed = bool(payload['allowed'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Bad payload'}, status=400)

    if codename not in __import__('accounts.permissions', fromlist=['capability_codenames']).capability_codenames():
        return JsonResponse({'error': 'Unknown capability'}, status=400)

    role = Role.objects.filter(pk=role_id).first()
    if role is None:
        return JsonResponse({'error': 'Unknown role'}, status=400)
    if role.name == Role.SUPER_ADMIN:
        return JsonResponse({'error': 'Super Admin permissions are fixed'}, status=400)

    RolePermission.objects.update_or_create(
        role=role, codename=codename, defaults={'allowed': allowed},
    )
    PermissionChangeLog.objects.create(
        actor=request.user, role=role, codename=codename, allowed=allowed,
    )
    return JsonResponse({'ok': True})
```

> Style note: prefer a top-of-file `from accounts.permissions import capability_codenames` and call it directly rather than the inline `__import__`; the inline form is shown only to keep this step self-contained.

- [ ] **Step 4: Add the route**

In `accounts/urls.py`, add:

```python
    path('settings/permissions/toggle/', views.ajax_toggle_permission, name='toggle_permission'),
```

- [ ] **Step 5: Replace the template `<script>` block**

In `templates/accounts/permission_matrix.html`, replace the placeholder `<script>` with:

```html
<script>
(function () {
  function csrf() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }
  document.querySelectorAll('.perm-toggle:not([disabled])').forEach(function (box) {
    box.addEventListener('change', function () {
      var cb = this;
      cb.disabled = true;
      fetch("{% url 'accounts:toggle_permission' %}", {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
        body: JSON.stringify({
          role: parseInt(cb.dataset.role, 10),
          codename: cb.dataset.codename,
          allowed: cb.checked,
        }),
      })
      .then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        cb.disabled = false;
        if (!res.ok) { cb.checked = !cb.checked; alert(res.j.error || 'Failed to save'); }
      })
      .catch(function () { cb.disabled = false; cb.checked = !cb.checked; alert('Network error'); });
    });
  });
})();
</script>
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python manage.py test accounts.tests.TogglePermissionTests -v 2`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add accounts/views.py accounts/urls.py templates/accounts/permission_matrix.html accounts/tests.py
git commit -m "Permissions: AJAX toggle endpoint + audit log + grid JS"
```

---

## Task 9: Wire Dashboard — access gate + finance region branch

**Files:**
- Modify: `dashboard/views.py` (`index`, lines ~50-61)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
class DashboardWiringTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()

    def test_finance_with_access_gets_200(self):
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)

    def test_finance_without_access_gets_403(self):
        role = Role.objects.get(name=Role.FINANCE_REP)
        RolePermission.objects.filter(role=role, codename='dashboard.access').update(allowed=False)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.DashboardWiringTests -v 2`
Expected: FAIL on `test_finance_without_access_gets_403` (currently returns 200 — no gate).

- [ ] **Step 3: Implement**

In `dashboard/views.py`, add the import at top:

```python
from accounts.permissions import require_capability
```

Decorate `index` and add the finance region branch. Replace:

```python
@login_required
def index(request):
    """Main dashboard view with regional tabs"""
    user = request.user

    # Base queryset based on user role
    if user.is_super_admin_user:
        projects = Project.objects.all()
    elif user.is_admin_user or user.is_manager_user:
        projects = Project.objects.filter(region=user.region)
    else:
        projects = Project.objects.filter(owner=user)
```

with:

```python
@login_required
@require_capability('dashboard.access')
def index(request):
    """Main dashboard view with regional tabs"""
    user = request.user

    # Base queryset based on user role
    if user.is_super_admin_user:
        projects = Project.objects.all()
    elif user.is_admin_user or user.is_manager_user:
        projects = Project.objects.filter(region=user.region)
    elif getattr(user, 'is_finance_team_user', False):
        # Finance sees their own region (capability gate already passed).
        projects = Project.objects.filter(region=user.region)
    else:
        projects = Project.objects.filter(owner=user)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test accounts.tests.DashboardWiringTests -v 2`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add dashboard/views.py accounts/tests.py
git commit -m "Permissions: gate dashboard on dashboard.access + finance region view"
```

---

## Task 10: Wire Commercial Pipeline — access gate + finance region branch

**Files:**
- Modify: `projects/views.py` (`ProjectPermissionMixin.get_queryset` lines 21-38; `ProjectListView`)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
from projects.models import Project, Region


class PipelineWiringTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.region = Region.objects.create(name='LNA', code='LNA')
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.region = self.region
        self.fin.save()

    def test_finance_with_access_sees_region_projects(self):
        owner = User.objects.create_user('owner', password='x')
        Project.objects.create(name='P1', region=self.region, owner=owner)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('projects:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'P1')

    def test_finance_without_access_403(self):
        role = Role.objects.get(name=Role.FINANCE_REP)
        RolePermission.objects.filter(role=role, codename='pipeline.access').update(allowed=False)
        self.client.force_login(self.fin)
        resp = self.client.get(reverse('projects:list'))
        self.assertEqual(resp.status_code, 403)
```

> Note: check `projects/models.py` for the exact `Region` constructor fields; adjust `Region.objects.create(...)` if `code`/`name` differ.

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.PipelineWiringTests -v 2`
Expected: FAIL — finance currently sees `owner=user` (no P1) and there is no 403 gate.

- [ ] **Step 3: Implement**

In `projects/views.py`, add import at top:

```python
from accounts.permissions import CapabilityRequiredMixin
```

Add the finance branch in `ProjectPermissionMixin.get_queryset` — replace the final `return queryset.filter(owner=user)` (line ~38) with:

```python
        if getattr(user, 'is_finance_team_user', False):
            # Finance sees their region (the pipeline.access gate runs first).
            return queryset.filter(region=user.region)
        return queryset.filter(owner=user)
```

Gate `ProjectListView` — change its class declaration to include the mixin and capability. Replace:

```python
class ProjectListView(ProjectPermissionMixin, ListView):
```

with:

```python
class ProjectListView(CapabilityRequiredMixin, ProjectPermissionMixin, ListView):
    capability = 'pipeline.access'
```

> `CapabilityRequiredMixin` must precede `ProjectPermissionMixin` so its `dispatch` runs the gate before the queryset loads.

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test accounts.tests.PipelineWiringTests -v 2`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add projects/views.py accounts/tests.py
git commit -m "Permissions: gate pipeline on pipeline.access + finance region view"
```

---

## Task 11: Wire Costing, Procurement, PO, DN access gates

**Files:**
- Modify: `costing/views.py` (`CostingListView`)
- Modify: `procurement/views.py` (`procurement_dashboard`, `POListView`, `DNListView`)
- Test: `accounts/tests.py`

- [ ] **Step 1: Write the failing test**

Add to `accounts/tests.py`:

```python
class ModuleAccessWiringTests(TestCase):
    def setUp(self):
        for name, _ in Role.ROLE_CHOICES:
            Role.objects.get_or_create(name=name)
        seed_default_permissions()
        self.fin = User.objects.create_user('fin', password='x')
        self.fin.role = Role.objects.get(name=Role.FINANCE_REP)
        self.fin.save()

    def test_finance_blocked_from_procurement(self):
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse('procurement:dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('procurement:po_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('procurement:dn_list')).status_code, 403)

    def test_finance_allowed_costing(self):
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse('costing:list')).status_code, 200)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test accounts.tests.ModuleAccessWiringTests -v 2`
Expected: FAIL — procurement views currently don't 403 finance.

- [ ] **Step 3: Implement**

In `costing/views.py`, add import and gate `CostingListView`:

```python
from accounts.permissions import CapabilityRequiredMixin
```

Change:

```python
class CostingListView(CostingPermissionMixin, ListView):
```

to:

```python
class CostingListView(CapabilityRequiredMixin, CostingPermissionMixin, ListView):
    capability = 'costing.access'
```

In `procurement/views.py`, add imports:

```python
from accounts.permissions import require_capability, CapabilityRequiredMixin
```

Decorate the function view — change:

```python
def procurement_dashboard(request):
```

(it already has a `@login_required`; add the capability decorator beneath it):

```python
@login_required
@require_capability('procurement.access')
def procurement_dashboard(request):
```

> If `procurement_dashboard` is wrapped with `login_required` in `urls.py` instead of a decorator, add `@require_capability('procurement.access')` directly above the `def` and confirm `request.user` is populated (it is, behind `login_required`).

Gate `POListView` — change its declaration to:

```python
class POListView(CapabilityRequiredMixin, ListView):   # keep existing base/mixins, prepend CapabilityRequiredMixin
    capability = 'po.access'
```

Gate `DNListView` similarly:

```python
class DNListView(CapabilityRequiredMixin, ListView):   # keep existing base/mixins, prepend CapabilityRequiredMixin
    capability = 'dn.access'
```

> Prepend `CapabilityRequiredMixin` to whatever base classes each view already lists; do not drop existing mixins.

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test accounts.tests.ModuleAccessWiringTests -v 2`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add costing/views.py procurement/views.py accounts/tests.py
git commit -m "Permissions: gate costing/procurement/po/dn on *.access"
```

---

## Task 12: Wire base.html nav with the `can` filter

**Files:**
- Modify: `templates/base.html` (Dashboard ~820, Commercial Pipeline ~825, Procurement section ~936, PO/DN links)
- Test: manual (template-render smoke; covered indirectly by `can` filter unit tests)

- [ ] **Step 1: Load the filter**

At the very top of `templates/base.html` (after any existing `{% load %}` lines), add:

```html
{% load perms %}
```

- [ ] **Step 2: Gate the Dashboard nav item**

Wrap the Dashboard `<li>` (around line 819-823) with:

```html
{% if user|can:'dashboard.nav' %}
<li class="nav-item">
  <a class="nav-link {% if request.resolver_match.url_name == 'index' and request.resolver_match.app_name == 'dashboard' %}active{% endif %}" href="{% url 'dashboard:index' %}" data-title="Dashboard">
    <i class="bi bi-speedometer2"></i> <span>Dashboard</span>
  </a>
</li>
{% endif %}
```

- [ ] **Step 3: Gate the Commercial Pipeline nav item**

Wrap the Commercial Pipeline `<li>` (around line 824-828) with `{% if user|can:'pipeline.nav' %} ... {% endif %}`.

- [ ] **Step 4: Gate the Procurement section + its links**

Wrap the Procurement `nav-section` header and its `<ul>` (starting around line 936) with `{% if user|can:'procurement.nav' %} ... {% endif %}`. Inside, wrap the Purchase Orders link with `{% if user|can:'po.nav' %}` and the Delivery Notes link with `{% if user|can:'dn.nav' %}`.

> Leave the Costing links (under Proposals → Commercial) gated by `{% if user|can:'costing.nav' %}` around the "Commercial" sub-group block (lines ~880-910). Do not remove the existing visual-state conditionals; only wrap with the capability check.

- [ ] **Step 5: Manual smoke check**

Run: `python manage.py runserver` and log in as a finance user (or use Django shell to confirm `user.has_capability('procurement.nav') is False`). Expected: finance sees Dashboard, Commercial Pipeline, Costing; does NOT see the Procurement section.

- [ ] **Step 6: Commit**

```bash
git add templates/base.html
git commit -m "Permissions: gate sidebar nav links with can filter"
```

---

## Task 13: Full-suite regression + final commit

- [ ] **Step 1: Run the whole accounts suite**

Run: `python manage.py test accounts -v 2`
Expected: all tests PASS.

- [ ] **Step 2: Run the broader suite to catch regressions**

Run: `python manage.py test -v 1`
Expected: no new failures introduced by the wiring (pre-existing failures, if any, unchanged).

- [ ] **Step 3: System check**

Run: `python manage.py check`
Expected: `System check identified no issues`.

- [ ] **Step 4: Manual end-to-end**

- Log in as super admin → open `/accounts/settings/permissions/` → toggle `procurement.access` ON for `finance_rep` → save (network 200).
- Log in as a finance user → confirm the Procurement section now appears and `/procurement/` returns 200.
- Toggle it back OFF → confirm 403 returns and the nav link disappears.

- [ ] **Step 5: Final commit (if any stragglers)**

```bash
git add -A
git commit -m "Permissions: phase 1 complete — access + nav capability enforcement"
```

---

## Self-review notes (addressed)

- **Spec coverage:** model (Task 2), registry (1), helper (3), decorator/mixin (4), filter (5), seed incl. finance-pipeline change (6), grid view (7), AJAX toggle + audit (8), phase-1 wiring across all listed modules (9-12), regression (13). Region/stage scoping left in code per spec.
- **Super-admin lockout safety:** grid view and toggle endpoint both hardcode `is_super_admin_user`; `has_capability` always-true for super admin; super-admin grid column locked + endpoint rejects toggling it (Task 8 `test_cannot_toggle_super_admin_row`).
- **Naming consistency:** `has_capability`, `require_capability`, `CapabilityRequiredMixin`, `can` (filter), `seed_default_permissions`, `capability_codenames`, `capabilities_by_module` used identically across tasks.
- **Known follow-ups (out of phase-1 scope):** granular edit/create/delete/export/approve enforcement; `prune_permissions` management command for orphan grants; a management command to re-seed newly added capabilities.
