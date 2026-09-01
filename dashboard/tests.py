import tempfile
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role, User
from costing.models import CostingSheet, ExchangeRate
from projects.models import Project, ProjectStatus, Region


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StorageReportTests(TestCase):
    """The Storage admin page and the orphan-preview endpoint are super-admin
    only; preview redirects to an existing object and 404s otherwise."""

    def setUp(self):
        sa, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.super = User.objects.create_user('sa', password='x')
        self.super.role = sa
        self.super.save()
        self.plain = User.objects.create_user('plain', password='x')

    def test_report_page_super_admin_only(self):
        self.client.force_login(self.plain)
        self.assertEqual(
            self.client.get(reverse('dashboard:storage_report')).status_code, 403)
        self.client.force_login(self.super)
        self.assertEqual(
            self.client.get(reverse('dashboard:storage_report')).status_code, 200)

    def test_preview_requires_super_admin(self):
        self.client.force_login(self.plain)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': 'x.txt'})
        self.assertEqual(r.status_code, 403)

    def test_preview_missing_key_404(self):
        self.client.force_login(self.super)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': 'nope/missing.txt'})
        self.assertEqual(r.status_code, 404)

    def test_preview_existing_file_redirects(self):
        name = default_storage.save('orphan-preview.txt', ContentFile(b'hello'))
        self.client.force_login(self.super)
        r = self.client.get(reverse('dashboard:storage_orphan_preview'),
                            {'key': name})
        self.assertEqual(r.status_code, 302)


class CostingValuedTileTests(TestCase):
    """Won and Hot Leads tiles report the ACTUAL costing-sheet contract total.

    The other tiles still sum estimated_value; only these two switch to the
    real priced number, resolved by the same rule the Commercial Pipeline list
    uses (costing sheet → actual_sales → estimate).
    """

    def setUp(self):
        self.lna = Region.objects.create(name='Saudi', code='LNA', currency='SAR')
        self.won = ProjectStatus.objects.create(name='Won', category='won')
        self.hot = ProjectStatus.objects.create(name='Hot', category='hot_lead')
        self.active = ProjectStatus.objects.create(name='Open', category='active')
        # rate_to_usd = units per 1 USD. (Rows may be migration-seeded.)
        ExchangeRate.objects.update_or_create(
            currency_code='SAR', defaults={'rate_to_usd': Decimal('3.75')})
        ExchangeRate.objects.update_or_create(
            currency_code='GBP', defaults={'rate_to_usd': Decimal('0.80')})
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.user = User.objects.create_user('boss', password='x')
        self.user.role = role
        self.user.region = self.lna
        self.user.save()

    def _project(self, ref, status, **kwargs):
        return Project.objects.create(project_name=ref, proposal_reference=ref,
                                      status=status, region=self.lna, **kwargs)

    def _stats(self, currency='SAR'):
        from dashboard.views import _resolve_sales_values, get_region_stats
        qs = Project.objects.all()
        sales_values, rates = _resolve_sales_values(qs)
        return get_region_stats(qs, ['LNA'], sales_values, currency, rates)

    def test_won_uses_costing_total_not_estimate(self):
        proj = self._project('W-1', self.won, estimated_value=Decimal('900000'))
        # scope_of_work_total feeds contract_total without building line items.
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('12500'))
        stats = self._stats()
        self.assertEqual(stats['won']['count'], 1)
        self.assertEqual(stats['won']['value'], Decimal('12500.00'))

    def test_won_falls_back_to_estimate_without_costing(self):
        self._project('W-2', self.won, estimated_value=Decimal('4000'))
        self.assertEqual(self._stats()['won']['value'], Decimal('4000.00'))

    def test_costing_total_converted_into_tile_currency(self):
        proj = self._project('W-3', self.won)
        # Sheet priced in GBP; an AED tile must convert: 800/0.80*3.67 = 3670.
        ExchangeRate.objects.update_or_create(
            currency_code='AED', defaults={'rate_to_usd': Decimal('3.67')})
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='GBP',
                                    scope_of_work_total=Decimal('800'))
        self.assertEqual(self._stats('AED')['won']['value'], Decimal('3670.00'))

    def test_hot_leads_uses_costing_total_not_estimate(self):
        proj = self._project('H-1', self.hot, estimated_value=Decimal('650000'))
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('8250'))
        stats = self._stats()
        self.assertEqual(stats['hot_leads']['count'], 1)
        self.assertEqual(stats['hot_leads']['value'], Decimal('8250.00'))

    def test_hot_leads_falls_back_to_estimate_without_costing(self):
        self._project('H-2', self.hot, estimated_value=Decimal('3200'))
        self.assertEqual(self._stats()['hot_leads']['value'], Decimal('3200.00'))

    def test_won_and_hot_leads_are_summed_separately(self):
        won_p = self._project('W-5', self.won)
        CostingSheet.objects.create(title='SW', project=won_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('1000'))
        hot_p = self._project('H-3', self.hot)
        CostingSheet.objects.create(title='SH', project=hot_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('2000'))
        stats = self._stats()
        self.assertEqual(stats['won']['value'], Decimal('1000.00'))
        self.assertEqual(stats['hot_leads']['value'], Decimal('2000.00'))

    def test_other_tiles_still_use_estimated_value(self):
        proj = self._project('A-1', self.active, estimated_value=Decimal('7000'))
        CostingSheet.objects.create(title='S', project=proj, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('99'))
        self.assertEqual(self._stats()['active']['value'], Decimal('7000'))

    def test_dashboard_page_renders_costing_values(self):
        won_p = self._project('W-4', self.won, estimated_value=Decimal('900000'))
        CostingSheet.objects.create(title='SW', project=won_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('12500'))
        hot_p = self._project('H-4', self.hot, estimated_value=Decimal('500000'))
        CostingSheet.objects.create(title='SH', project=hot_p, created_by=self.user,
                                    output_currency='SAR',
                                    scope_of_work_total=Decimal('6400'))
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'SAR 12,500.00')
        self.assertContains(resp, 'SAR 6,400.00')
        # Won/Hot Leads show the costing total; the Total tile is untouched and
        # still reports the estimate, so assert on the tiles, not the whole page.
        # Region context is now a list of dynamic tabs, not a fixed
        # 'lna' key - find LNA's entry and use its stats dict.
        lna_tab = next(t for t in resp.context['region_tabs'] if t['name'] == 'LNA')
        lna = lna_tab['stats']
        self.assertEqual(lna['won']['value'], Decimal('12500.00'))
        self.assertEqual(lna['hot_leads']['value'], Decimal('6400.00'))
        self.assertEqual(lna['total']['value'], Decimal('1400000'))
        # Charts must agree with the tiles they sit beside.
        self.assertEqual(resp.context['chart_data']['lna']['won_value'], 12500.0)
        self.assertEqual(resp.context['chart_data']['lna']['hot_leads_value'], 6400.0)


class DashboardRegionScopingTests(TestCase):
    """Region confidentiality on the main Sales Pipeline Dashboard: a
    Super Admin sees every region tab with real figures; everyone else
    sees real figures only for their own region, with every other region
    still appearing as a tab but showing no real numbers (can_view=False,
    stats=None). Also covers the sales-rep union scoping and dynamic tab
    grouping, including that a newly created region shows up automatically."""

    def setUp(self):
        self.lna = Region.objects.create(name='Leap Arabia', code='LNATEST', currency='SAR')
        self.zoneb = Region.objects.create(name='Zone B', code='ZBTEST', currency='USD')
        self.status = ProjectStatus.objects.create(name='Active', category='active', is_active=True)

        self.lna_project = Project.objects.create(
            project_name='LNA Deal', proposal_reference='LNA-D1', region=self.lna, status=self.status)
        self.zoneb_project = Project.objects.create(
            project_name='Zone B Deal', proposal_reference='ZB-D1', region=self.zoneb, status=self.status)

        super_role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        manager_role, _ = Role.objects.get_or_create(name=Role.MANAGER)
        rep_role, _ = Role.objects.get_or_create(name=Role.SALES_REP)

        from accounts.permissions import seed_default_permissions
        seed_default_permissions()

        self.super = User.objects.create_user('dashsuper', password='x')
        self.super.role = super_role
        self.super.save()

        self.manager = User.objects.create_user('dashmanager', password='x')
        self.manager.role = manager_role
        self.manager.region = self.lna
        self.manager.save()

        self.rep_with_region = User.objects.create_user('dashrep1', password='x')
        self.rep_with_region.role = rep_role
        self.rep_with_region.region = self.lna
        self.rep_with_region.save()

        self.rep_no_region = User.objects.create_user('dashrep2', password='x')
        self.rep_no_region.role = rep_role
        self.rep_no_region.save()

    def test_super_admin_sees_all_regions_with_real_data(self):
        self.client.force_login(self.super)
        resp = self.client.get(reverse('dashboard:index'))
        tabs = {t['name']: t for t in resp.context['region_tabs']}
        self.assertTrue(tabs['LNATEST']['can_view'])
        self.assertTrue(tabs['ZBTEST']['can_view'])
        self.assertIsNotNone(tabs['LNATEST']['stats'])
        self.assertIsNotNone(tabs['ZBTEST']['stats'])

    def test_manager_sees_own_region_real_others_locked(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('dashboard:index'))
        tabs = {t['name']: t for t in resp.context['region_tabs']}
        self.assertTrue(tabs['LNATEST']['can_view'])
        self.assertIsNotNone(tabs['LNATEST']['stats'])
        self.assertFalse(tabs['ZBTEST']['can_view'])
        self.assertIsNone(tabs['ZBTEST']['stats'])

    def test_locked_tab_still_appears_but_with_no_chart_data(self):
        self.client.force_login(self.manager)
        resp = self.client.get(reverse('dashboard:index'))
        names = [t['name'] for t in resp.context['region_tabs']]
        self.assertIn('ZBTEST', names)
        self.assertNotIn('zbtest', resp.context['chart_data'])

    def test_sales_rep_sees_union_of_owned_and_own_region(self):
        self.zoneb_project.owner = self.rep_with_region
        self.zoneb_project.save()
        self.client.force_login(self.rep_with_region)
        resp = self.client.get(reverse('dashboard:index'))
        tabs = {t['name']: t for t in resp.context['region_tabs']}
        self.assertTrue(tabs['LNATEST']['can_view'])
        # Zone B tab is still locked as a region even though the rep owns
        # a project there - the tab lock is about that region's aggregate
        # figures, separate from which projects they personally own.
        self.assertFalse(tabs['ZBTEST']['can_view'])

    def test_sales_rep_with_no_region_sees_only_owned_projects(self):
        self.lna_project.owner = self.rep_no_region
        self.lna_project.save()
        self.client.force_login(self.rep_no_region)
        resp = self.client.get(reverse('dashboard:index'))
        self.assertEqual(resp.status_code, 200)
        tabs = {t['name']: t for t in resp.context['region_tabs']}
        self.assertFalse(tabs['LNATEST']['can_view'])
        self.assertFalse(tabs['ZBTEST']['can_view'])

    def test_grouped_regions_combine_into_one_tab(self):
        Region.objects.create(name='UK', code='UKTEST', currency='GBP', dashboard_group='LNUKTEST')
        Region.objects.create(name='Global', code='GLBTEST', currency='GBP', dashboard_group='LNUKTEST')
        self.client.force_login(self.super)
        resp = self.client.get(reverse('dashboard:index'))
        names = [t['name'] for t in resp.context['region_tabs']]
        self.assertEqual(names.count('LNUKTEST'), 1)
        self.assertNotIn('UKTEST', names)
        self.assertNotIn('GLBTEST', names)

    def test_newly_created_region_appears_as_a_tab_automatically(self):
        Region.objects.create(name='Zone D', code='ZDTEST', currency='EUR')
        self.client.force_login(self.super)
        resp = self.client.get(reverse('dashboard:index'))
        names = [t['name'] for t in resp.context['region_tabs']]
        self.assertIn('ZDTEST', names)


class DashboardRegionEdgeCaseTests(TestCase):
    """Extra edge cases for the dynamic region grouping on the main
    dashboard: inactive regions must not appear at all, and whitespace
    differences in dashboard_group must not split what should be one
    combined tab into two."""

    def setUp(self):
        role, _ = Role.objects.get_or_create(name=Role.SUPER_ADMIN)
        self.super = User.objects.create_user('dashedge', password='x')
        self.super.role = role
        self.super.save()
        self.client.force_login(self.super)

    def test_inactive_region_does_not_appear_as_a_tab(self):
        Region.objects.create(name='Retired Zone', code='RETTEST', currency='USD', is_active=False)
        resp = self.client.get(reverse('dashboard:index'))
        names = [t['name'] for t in resp.context['region_tabs']]
        self.assertNotIn('RETTEST', names)

    def test_dashboard_group_whitespace_still_groups_correctly(self):
        Region.objects.create(name='Space UK', code='SPUKTEST', currency='GBP', dashboard_group='  SPACEGROUP  ')
        Region.objects.create(name='Space Global', code='SPGLBTEST', currency='GBP', dashboard_group='SPACEGROUP')
        resp = self.client.get(reverse('dashboard:index'))
        names = [t['name'] for t in resp.context['region_tabs']]
        # Both regions combine into ONE tab keyed by the stripped group
        # name, not split into two due to incidental whitespace.
        self.assertEqual(names.count('SPACEGROUP'), 1)


class CodebaseHealthChecksTests(TestCase):
    """Automated checks for bug classes that have actually bitten this
    codebase before - not specific to any one feature, but cheap to run
    on every test invocation and catch mistakes that plain syntax checks
    (manage.py check, py_compile) do not."""

    def _iter_files(self, extensions):
        import os
        from django.conf import settings
        base = str(settings.BASE_DIR)
        skip_dirs = {'venv', 'node_modules', '.git', '__pycache__', 'staticfiles', 'media'}
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if any(fname.endswith(ext) for ext in extensions):
                    yield os.path.join(dirpath, fname)

    def test_no_leftover_git_conflict_markers(self):
        """A stray <<<<<<< / ======= / >>>>>>> from an unresolved or
        badly-resolved merge should never make it into the codebase.
        Matches git's actual format precisely (a line starting with
        <<<<<<< or >>>>>>>, or a line that is exactly ======= once
        stripped) - a plain substring check false-positives on this
        file's own marker string literals, and on long "====" comment
        dividers used elsewhere in the codebase."""
        import os
        this_file = os.path.abspath(__file__)
        offenders = []
        for path in self._iter_files(['.py', '.html']):
            if os.path.abspath(path) == this_file:
                continue
            try:
                with open(path, encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except Exception:
                continue
            for lineno, line in enumerate(lines, 1):
                stripped = line.strip()
                if (stripped.startswith('<<<<<<<') or stripped.startswith('>>>>>>>')
                        or stripped == '======='):
                    offenders.append(f"{path}:{lineno}: {stripped[:80]}")
        self.assertEqual(
            offenders, [],
            "Found leftover git conflict markers:\n" + "\n".join(offenders))

    def test_no_broken_multiline_django_comments(self):
        """{# ... #} is a single-line Django comment tag - if {# opens
        without a matching #} on the same line, everything after it
        renders as literal visible text on the page (the KPI New tab bug
        from earlier this session)."""
        import re
        offenders = []
        pattern = re.compile(r'\{#(?!.*#\})')
        for path in self._iter_files(['.html']):
            try:
                with open(path, encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except Exception:
                continue
            for lineno, line in enumerate(lines, 1):
                if pattern.search(line):
                    offenders.append(f"{path}:{lineno}: {line.strip()[:80]}")
        self.assertEqual(
            offenders, [],
            "Found Django comment tags that don't close on the same line "
            "(renders as visible text on the page):\n" + "\n".join(offenders))

    def test_all_templates_have_valid_syntax(self):
        """Every template must at least load and parse cleanly - catches
        unclosed tags and mismatched {% if %}/{% endif %} pairs that
        otherwise only surface when a specific view happens to render
        that exact template."""
        import os
        from django.conf import settings
        from django.template.loader import get_template
        from django.template import TemplateSyntaxError
        offenders = []
        template_dirs = settings.TEMPLATES[0].get('DIRS', [])
        for template_dir in template_dirs:
            template_dir = str(template_dir)
            for dirpath, _, filenames in os.walk(template_dir):
                for fname in filenames:
                    if not fname.endswith('.html'):
                        continue
                    full_path = os.path.join(dirpath, fname)
                    rel_path = os.path.relpath(full_path, template_dir).replace('\\', '/')
                    try:
                        get_template(rel_path)
                    except TemplateSyntaxError as e:
                        offenders.append(f"{rel_path}: {e}")
                    except Exception:
                        pass
        self.assertEqual(
            offenders, [],
            "Found templates with syntax errors:\n" + "\n".join(offenders))
