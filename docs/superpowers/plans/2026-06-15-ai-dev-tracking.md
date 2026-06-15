# AI Development Tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** New `devtracking` app: assign tasks to developers, auto-time completion, live GitHub PR status, AI admin reports. Spec: `docs/superpowers/specs/2026-06-15-ai-dev-tracking-design.md`.

**Tech:** Django 6, `python manage.py test` (NOT pytest), Windows. Mirror existing apps (`hr`, `costing`) for view/template/url patterns. Append app tests in `devtracking/tests.py`.

**Conventions:**
- App registered in `erp_leap/settings.py` INSTALLED_APPS; urls included in `erp_leap/urls.py` as `path('devtracking/', include('devtracking.urls'))`.
- Permission gating via `accounts.permissions` (CapabilityRequiredMixin / `@require_capability`). Region not relevant here.
- `notify_users(...)` lives in `notifications/services.py`.
- Anthropic/GitHub/Render env vars are all OPTIONAL — code must degrade gracefully, never crash, when absent.

**Shared test helpers (reuse):**
```python
from accounts.models import Role, User

def mkuser(username, role_name):
    role, _ = Role.objects.get_or_create(name=role_name)
    u = User.objects.create_user(username, password='x'); u.role = role; u.save()
    return u
```

---

## Task 1: App scaffold + DEVELOPER role + models + migrations

**Files:** `accounts/models.py`, `devtracking/` (new app), `erp_leap/settings.py`, `erp_leap/urls.py`, `devtracking/tests.py`

- [ ] **Step 1: DEVELOPER role.** In `accounts/models.py` `Role`: add `DEVELOPER = 'developer'`, append `(DEVELOPER, 'Developer')` to `ROLE_CHOICES`, add property:
```python
    @property
    def is_developer(self):
        return self.name == self.DEVELOPER
```
On the `User` model (after the other `is_*_user` props), add:
```python
    @property
    def is_developer_user(self):
        return bool(self.role and self.role.is_developer)
```

- [ ] **Step 2: Create app.** `python manage.py startapp devtracking`. Add `'devtracking'` to `INSTALLED_APPS` in `erp_leap/settings.py`. Create `devtracking/urls.py` with `app_name = 'devtracking'` and an empty `urlpatterns = []` for now. In `erp_leap/urls.py` add `path('devtracking/', include('devtracking.urls'))`.

- [ ] **Step 3: Failing model test** — write `devtracking/tests.py`:
```python
from datetime import date, timedelta
from django.test import TestCase
from django.utils import timezone
from accounts.models import Role, User


def mkuser(username, role_name):
    role, _ = Role.objects.get_or_create(name=role_name)
    u = User.objects.create_user(username, password='x'); u.role = role; u.save()
    return u


class DevTaskModelTests(TestCase):
    def setUp(self):
        self.admin = mkuser('adm', Role.ADMIN)
        self.dev = mkuser('dev', Role.DEVELOPER)

    def _task(self, **kw):
        from devtracking.models import DevTask
        kw.setdefault('title', 'T'); kw.setdefault('developer', self.dev)
        kw.setdefault('assigned_by', self.admin)
        return DevTask.objects.create(**kw)

    def test_mark_started_stamps_once(self):
        t = self._task()
        t.mark_started(); first = t.started_at
        self.assertIsNotNone(first); self.assertEqual(t.status, 'in_progress')
        t.mark_started()  # idempotent
        self.assertEqual(t.started_at, first)

    def test_mark_done_stamps_and_on_time(self):
        t = self._task(due_date=date.today() + timedelta(days=2))
        t.mark_started(); t.mark_done()
        self.assertEqual(t.status, 'done')
        self.assertIsNotNone(t.completed_at)
        self.assertTrue(t.on_time)

    def test_overdue(self):
        t = self._task(due_date=date.today() - timedelta(days=1))
        self.assertTrue(t.is_overdue)
        t.mark_started(); t.mark_done()
        self.assertFalse(t.is_overdue)  # done is never "overdue"
```

- [ ] **Step 4: Run** `python manage.py test devtracking.tests.DevTaskModelTests -v 2` → FAIL (no models).

- [ ] **Step 5: Implement models** — `devtracking/models.py`:
```python
from django.db import models
from django.conf import settings
from django.utils import timezone


class DevTask(models.Model):
    PRIORITY_CHOICES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]
    STATUS_CHOICES = [('assigned', 'Assigned'), ('in_progress', 'In progress'),
                      ('blocked', 'Blocked'), ('done', 'Done')]

    developer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='dev_tasks')
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='dev_tasks_assigned')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='assigned')
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    github_url = models.URLField(blank=True)
    gh_state = models.CharField(max_length=10, blank=True)   # open/closed/merged
    gh_commits = models.PositiveIntegerField(null=True, blank=True)
    gh_title = models.CharField(max_length=300, blank=True)
    gh_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} → {self.developer}'

    def mark_started(self):
        if self.started_at is None:
            self.started_at = timezone.now()
        if self.status in ('assigned', 'blocked'):
            self.status = 'in_progress'
        self.save()

    def mark_done(self):
        if self.started_at is None:
            self.started_at = timezone.now()
        self.completed_at = timezone.now()
        self.status = 'done'
        self.save()

    def mark_blocked(self):
        self.status = 'blocked'
        self.save()

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status != 'done'
                    and self.due_date < timezone.now().date())

    @property
    def on_time(self):
        if self.status != 'done' or not self.completed_at or not self.due_date:
            return None
        return self.completed_at.date() <= self.due_date

    @property
    def elapsed(self):
        if not self.started_at:
            return None
        end = self.completed_at or timezone.now()
        return end - self.started_at

    @property
    def is_stuck(self):
        if self.status != 'in_progress' or not self.started_at:
            return False
        return (timezone.now() - self.started_at).days >= 3


class DevTaskUpdate(models.Model):
    task = models.ForeignKey(DevTask, on_delete=models.CASCADE, related_name='updates')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    note = models.TextField(blank=True)
    status_changed_to = models.CharField(max_length=12, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class DevDigest(models.Model):
    period_date = models.DateField()
    scope = models.CharField(max_length=20, default='all')  # 'all' or str(developer_id)
    content = models.TextField()
    model_used = models.CharField(max_length=60, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-generated_at']
```

- [ ] **Step 6: Migrate** — `makemigrations accounts devtracking` (accounts: the role choice is a no-op DB-wise but the migration records the choices change; devtracking: initial). `migrate`.

- [ ] **Step 7: Admin** — register all three in `devtracking/admin.py` (`list_display` with developer/status/due_date for DevTask).

- [ ] **Step 8: Run** `python manage.py test devtracking -v 2` → PASS. `python manage.py check`.

- [ ] **Step 9: Commit** `git add accounts/ devtracking/ erp_leap/ && git commit -m "devtracking: app scaffold, DEVELOPER role, task/update/digest models"`

---

## Task 2: Capabilities + nav + seed

**Files:** `accounts/permissions.py`, `templates/base.html`, migration/seed, `devtracking/tests.py`

- [ ] **Step 1:** Read `accounts/permissions.py` to learn the exact helper that registers a module's capabilities (the `_module(...)`/`CAPABILITIES` pattern) and `DEFAULT_MODULE_ACCESS`. Add a `devtracking` module exposing codenames `devtracking.admin` and `devtracking.mywork`. Grant in `DEFAULT_MODULE_ACCESS`: `super_admin`/`admin` get both; `developer` gets `devtracking.mywork`. Follow whatever seeding mechanism exists (a data migration calling `seed_default_permissions()`, or the existing seed command) so the rows are created — mirror how an existing module did it.

- [ ] **Step 2: Nav** — in `templates/base.html`, add under the Administration section a link gated `{% if user|can:'devtracking.admin' %}` → `{% url 'devtracking:dashboard' %}` labelled "Dev Tracking" (icon e.g. `bi-kanban`). Add a separate "My Tasks" link gated `{% if user|can:'devtracking.mywork' %}` → `{% url 'devtracking:my_tasks' %}` (place near My Work). Match the existing nav-item markup.

- [ ] **Step 3: Test** — append:
```python
class CapabilityTests(TestCase):
    def test_developer_has_mywork_not_admin(self):
        dev = mkuser('d2', Role.DEVELOPER)
        self.assertTrue(dev.has_capability('devtracking.mywork'))
        self.assertFalse(dev.has_capability('devtracking.admin'))

    def test_admin_has_both(self):
        adm = mkuser('a2', Role.ADMIN)
        self.assertTrue(adm.has_capability('devtracking.admin'))
        self.assertTrue(adm.has_capability('devtracking.mywork'))
```
(Confirm the real method name — `has_capability` — by reading the User model; adjust if different.)

- [ ] **Step 4: Run** the test → PASS (after seeding). `python manage.py check`. Commit `git commit -m "devtracking: capabilities + nav"`.

---

## Task 3: Admin views — dashboard shell, assign, task list, per-dev detail

**Files:** `devtracking/views.py`, `devtracking/forms.py`, `devtracking/urls.py`, `templates/devtracking/*.html`, `devtracking/tests.py`

- [ ] **Step 1:** `DevTaskForm` (ModelForm: developer, title, description, priority, estimated_hours, due_date, github_url) — `developer` queryset = `User.objects.filter(role__name=Role.DEVELOPER)`. Bootstrap widget classes like other forms.

- [ ] **Step 2: Views** (use `CapabilityRequiredMixin` with `capability = 'devtracking.admin'`):
  - `DashboardView` (TemplateView): context = per-developer counts (`assigned`/`in_progress`/`done`/`overdue`) computed from `DevTask`, list of overdue + stuck tasks, latest `DevDigest` (scope='all'). Template `devtracking/dashboard.html`.
  - `TaskAssignView` (CreateView, `DevTaskForm`) → on `form_valid` set `assigned_by=request.user`, `notify_users(recipients=[task.developer], verb='assigned you a task', actor=request.user, description=task.title, target_url=...)`. success → task list.
  - `TaskListView` (ListView) with filters `?developer=&status=&overdue=1`.
  - `DevDetailView`: a developer's tasks + their update timeline. (Take developer pk in URL.)
  Wire `devtracking/urls.py`: `dashboard/` (name `dashboard`, root `''`), `assign/`, `tasks/`, `developer/<int:pk>/`.

- [ ] **Step 3: Templates** — mirror `hr`/`costing` list/form styling. Show per-task: status badge, due date, on-time/late + elapsed, GitHub state badge if `gh_state`.

- [ ] **Step 4: Tests** — append:
```python
class AssignFlowTests(TestCase):
    def setUp(self):
        self.admin = mkuser('adm', Role.ADMIN); self.dev = mkuser('dev', Role.DEVELOPER)

    def test_assign_creates_task_and_notifies(self):
        self.client.force_login(self.admin)
        from django.urls import reverse
        resp = self.client.post(reverse('devtracking:assign'), {
            'developer': self.dev.pk, 'title': 'Build login', 'description': '',
            'priority': 'high', 'estimated_hours': '6', 'due_date': '2026-07-01', 'github_url': ''})
        from devtracking.models import DevTask
        self.assertEqual(DevTask.objects.filter(developer=self.dev, title='Build login').count(), 1)

    def test_developer_cannot_assign(self):
        self.client.force_login(self.dev)
        from django.urls import reverse
        resp = self.client.get(reverse('devtracking:assign'))
        self.assertIn(resp.status_code, (302, 403))  # blocked by capability
```

- [ ] **Step 5: Run** `python manage.py test devtracking -v 1` → PASS. `check`. Commit `git commit -m "devtracking: admin dashboard, assign, task list, per-dev detail"`.

---

## Task 4: Developer "My Tasks" + Start/Done/Blocked/Note

**Files:** `devtracking/views.py`, `devtracking/urls.py`, `templates/devtracking/my_tasks.html`, `devtracking/tests.py`

- [ ] **Step 1: Views** (gate `devtracking.mywork`):
  - `MyTasksView` (ListView) → `DevTask.objects.filter(developer=request.user)` grouped by status.
  - `task_action(request, pk)` (POST, `@require_capability('devtracking.mywork')`): only the task's own `developer` (or an admin) may act; reads `action` in {`start`,`done`,`blocked`} → calls the matching `mark_*()`; reads optional `note` → creates `DevTaskUpdate(task, author=request.user, note=note, status_changed_to=task.status)`. Reject acting on someone else's task (403). Redirect back to My Tasks.
- [ ] **Step 2: URLs** `my-tasks/` (name `my_tasks`), `tasks/<int:pk>/action/` (name `task_action`).
- [ ] **Step 3: Template** `my_tasks.html`: per task, Start/Done/Blocked buttons (POST form with `action`) + a one-line note input. Show elapsed/on-time once done.
- [ ] **Step 4: Tests** — append:
```python
class MyTasksActionTests(TestCase):
    def setUp(self):
        self.admin = mkuser('adm', Role.ADMIN)
        self.dev = mkuser('dev', Role.DEVELOPER); self.other = mkuser('o', Role.DEVELOPER)
        from devtracking.models import DevTask
        self.task = DevTask.objects.create(title='T', developer=self.dev, assigned_by=self.admin)

    def _act(self, user, action, note=''):
        self.client.force_login(user)
        from django.urls import reverse
        return self.client.post(reverse('devtracking:task_action', kwargs={'pk': self.task.pk}),
                                {'action': action, 'note': note})

    def test_start_then_done_stamps_and_logs_note(self):
        self._act(self.dev, 'start', 'beginning')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.assertIsNotNone(self.task.started_at)
        self._act(self.dev, 'done', 'finished')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'done')
        self.assertIsNotNone(self.task.completed_at)
        from devtracking.models import DevTaskUpdate
        self.assertEqual(DevTaskUpdate.objects.filter(task=self.task).count(), 2)

    def test_other_dev_cannot_act(self):
        self.assertEqual(self._act(self.other, 'start').status_code, 403)
```
- [ ] **Step 5: Run** → PASS. `check`. Commit `git commit -m "devtracking: My Tasks self-service (start/done/blocked + notes)"`.

---

## Task 5: AI digest service + settings + command + Generate-now

**Files:** `requirements.txt`, `erp_leap/settings.py`, `devtracking/ai.py`, `devtracking/views.py`, `devtracking/management/commands/generate_dev_digest.py`, `templates/devtracking/dashboard.html`, `devtracking/tests.py`

- [ ] **Step 1:** Add `anthropic` to `requirements.txt`. `pip install anthropic`. In `settings.py` add: `ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')`, `DEVTRACKING_AI_MODEL = os.environ.get('DEVTRACKING_AI_MODEL', 'claude-sonnet-4-6')` (confirm `os` import + the settings env-reading pattern already in the file).

- [ ] **Step 2:** `devtracking/ai.py`:
  - `build_digest_context(period=None)` → dict per developer with tasks-by-status, completed/assigned counts, on-time rate, avg elapsed hours, estimate-vs-actual, overdue + stuck lists, recent update notes. Pure, no API.
  - `_fallback_text(ctx)` → readable plain-text rendering of the context with header "AI summary unavailable — set ANTHROPIC_API_KEY for narrative reports."
  - `generate_admin_digest(generated_by=None)`:
    ```python
    ctx = build_digest_context()
    key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not key:
        content, model = _fallback_text(ctx), ''
    else:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            model = settings.DEVTRACKING_AI_MODEL
            msg = client.messages.create(model=model, max_tokens=1500,
                messages=[{'role': 'user', 'content': _prompt(ctx)}])
            content = msg.content[0].text
        except Exception:
            content, model = _fallback_text(ctx), ''
    from devtracking.models import DevDigest
    return DevDigest.objects.create(period_date=timezone.now().date(), scope='all',
                                    content=content, model_used=model, generated_by=generated_by)
    ```
  - `_prompt(ctx)` builds an instruction asking for: per-dev progress summary, overdue/stuck alerts, time & velocity insight, an overall rollup — from the JSON-ish context.

- [ ] **Step 3:** `generate_now` view (POST, gate `devtracking.admin`) → `generate_admin_digest(generated_by=request.user)` → redirect to dashboard. Management command `generate_dev_digest` → calls `generate_admin_digest()` and prints the digest id. Add a "Generate now" button on `dashboard.html` and render the latest digest's `content` (use `linebreaksbr` or a markdown filter if available; plain `<pre>`-style is fine).

- [ ] **Step 4: Tests** (NO network — rely on the no-key fallback):
```python
class DigestTests(TestCase):
    def setUp(self):
        self.admin = mkuser('adm', Role.ADMIN); self.dev = mkuser('dev', Role.DEVELOPER)
        from devtracking.models import DevTask
        DevTask.objects.create(title='A', developer=self.dev, assigned_by=self.admin, status='done')

    def test_context_counts(self):
        from devtracking.ai import build_digest_context
        ctx = build_digest_context()
        self.assertTrue(any(d['done'] >= 1 for d in ctx['developers']))

    def test_generate_fallback_without_key(self):
        from django.test import override_settings
        with override_settings(ANTHROPIC_API_KEY=''):
            from devtracking.ai import generate_admin_digest
            dg = generate_admin_digest(generated_by=self.admin)
            self.assertTrue(dg.content)
            self.assertEqual(dg.model_used, '')
```
(Adjust `ctx` shape assertions to your real return structure.)

- [ ] **Step 5: Run** → PASS. `check`. Commit `git commit -m "devtracking: AI digest service + generate command + dashboard panel"`.

---

## Task 6: Live GitHub PR status

**Files:** `requirements.txt`, `erp_leap/settings.py`, `devtracking/github.py`, `devtracking/views.py`, templates, `devtracking/tests.py`

- [ ] **Step 1:** Add `requests` to `requirements.txt`; `pip install requests`. `settings.py`: `GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')`.

- [ ] **Step 2:** `devtracking/github.py`:
```python
import re
import requests
from django.conf import settings
from django.utils import timezone

_PR_RE = re.compile(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)')

def parse_pr_url(url):
    m = _PR_RE.search(url or '')
    return (m.group(1), m.group(2), int(m.group(3))) if m else None

def fetch_pr_status(url):
    parsed = parse_pr_url(url)
    if not parsed:
        return None
    owner, repo, number = parsed
    headers = {'Accept': 'application/vnd.github+json'}
    token = getattr(settings, 'GITHUB_TOKEN', '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        r = requests.get(f'https://api.github.com/repos/{owner}/{repo}/pulls/{number}',
                         headers=headers, timeout=6)
        if r.status_code != 200:
            return None
        d = r.json()
        state = 'merged' if d.get('merged') else d.get('state', '')
        return {'state': state, 'commits': d.get('commits'), 'title': d.get('title', '')}
    except Exception:
        return None

def refresh_task_github(task):
    status = fetch_pr_status(task.github_url)
    if status is None:
        return False
    task.gh_state = status['state'] or ''
    task.gh_commits = status['commits']
    task.gh_title = (status['title'] or '')[:300]
    task.gh_checked_at = timezone.now()
    task.save(update_fields=['gh_state', 'gh_commits', 'gh_title', 'gh_checked_at'])
    return True
```

- [ ] **Step 3:** In the task detail/list view, refresh stale tasks: if `task.github_url` and (`gh_checked_at` is None or older than 15 min), call `refresh_task_github(task)` (wrap in try/except; never block the page). Add a "Refresh GitHub" POST action (gate admin or the owner) calling `refresh_task_github`. Render a badge from `gh_state` (open=blue, merged=purple, closed=grey) + commit count + `gh_title` tooltip.

- [ ] **Step 4: Tests** (mock the network):
```python
class GithubStatusTests(TestCase):
    def test_parse_pr_url(self):
        from devtracking.github import parse_pr_url
        self.assertEqual(parse_pr_url('https://github.com/acme/repo/pull/42'), ('acme', 'repo', 42))
        self.assertIsNone(parse_pr_url('https://example.com/x'))

    def test_refresh_writes_cache(self):
        from unittest.mock import patch
        from devtracking.models import DevTask
        from accounts.models import Role
        dev = mkuser('dev', Role.DEVELOPER)
        t = DevTask.objects.create(title='T', developer=dev,
                                   github_url='https://github.com/acme/repo/pull/7')
        with patch('devtracking.github.fetch_pr_status',
                   return_value={'state': 'merged', 'commits': 3, 'title': 'Add auth'}):
            from devtracking.github import refresh_task_github
            self.assertTrue(refresh_task_github(t))
        t.refresh_from_db()
        self.assertEqual(t.gh_state, 'merged'); self.assertEqual(t.gh_commits, 3)

    def test_no_token_no_crash(self):
        from devtracking.github import fetch_pr_status
        # non-PR url returns None without any network call
        self.assertIsNone(fetch_pr_status('not a url'))
```

- [ ] **Step 5: Run** → PASS. `check`. Commit `git commit -m "devtracking: live GitHub PR status (cached, graceful fallback)"`.

---

## Task 7: Regression + final review

- [ ] `python manage.py test devtracking -v 1` and `python manage.py test -v 1` (no new failures). `python manage.py check`. `python manage.py makemigrations --check --dry-run` (No changes).
- [ ] Final holistic review (opus): capability gating airtight (devs can't reach admin views or act on others' tasks); AI + GitHub both degrade gracefully with no keys (no crashes, no network in tests); timestamps stamp once; notifications fire on assign. SHIP / FIX-FIRST.

---

## Self-review notes
- **Spec coverage:** role+models (T1); caps+nav (T2); assign/list/detail/dashboard (T3); My-Tasks self-service+timing (T4); AI digest+command+fallback (T5); live GitHub status+fallback (T6); regression+review (T7).
- **Graceful degradation:** AI (no `ANTHROPIC_API_KEY` → fallback text) and GitHub (no `GITHUB_TOKEN` / bad URL → None) never crash; tests never hit the network.
- **Naming:** `devtracking`, `DevTask`/`DevTaskUpdate`/`DevDigest`, caps `devtracking.admin`/`devtracking.mywork`, url names `dashboard`/`assign`/`tasks`/`my_tasks`/`task_action`/`generate_now`, `mark_started/done/blocked`, `gh_*` cache fields — consistent across model/view/template/test.
- **Migrations:** T1 only (accounts role choice + devtracking initial). T2 seeding via the existing permission-seed mechanism.
