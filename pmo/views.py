"""Project delivery — the overview board and one project's milestone WBS."""
import calendar
import json
import zipfile
from decimal import Decimal, InvalidOperation
from functools import wraps
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from dashboard.views import projects_visible_to
from projects.models import Project

from .forms import (FaultLossEntryForm, ManpowerDetailsForm, NewEmployeeManpowerForm,
                     ProjectIssueForm,
                     GradeStructureLineFormSet, DesignationManpowerLineFormSet,
                     EmploymentCostAssumptionsForm, FirstYearMaintenanceLineFormSet,
                     ManpowerCostingHeaderForm, ProjectPOBasicsForm)
from .models import (ONE, ZERO, CommunicationMatrix, FaultLossEntry, ManpowerResource,
                      MilestoneProgressEntry, ProjectIssue, ProjectMilestone,
                      ResponsibilityMatrix, default_communication_columns,
                      default_responsibility_columns, sanitize_grid,
                      GradeStructureLine, DesignationManpowerLine, EmploymentCostAssumptions,
                      FirstYearMaintenanceLine, ManpowerCostingHeader)
from .progress import (board_row, board_rows, leaves, milestone_checklist,
                       project_completion, validate_weightages)
from .workbook_import import (deserialise_activities, plan_workbook,
                              serialise_activities, write_activities)


def can_see_delivery(user):
    """Who gets the Project Management department.

    Project Manager and Site Manager are the delivery roles; the rest is the
    usual oversight ladder. Kept as one function so the sidebar, the board and
    the update endpoint cannot disagree about who is allowed in — the workbook
    equivalent was a shared drive with no answer to this at all.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(
        user.is_super_admin_user
        or user.is_admin_user
        or user.is_manager_user
        or user.is_project_manager_user
        or user.is_site_manager_user
    )


def can_update_progress(user):
    """Who may move a completion figure.

    Narrower than viewing on purpose: the whole company's delivery reporting
    is derived from these numbers, so read access and write access are not the
    same decision.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(
        user.is_super_admin_user
        or user.is_admin_user
        or user.is_project_manager_user
        or user.is_site_manager_user
    )


def delivery_required(view_func):
    """Gate a view behind can_see_delivery.

    The PM Dashboard, Manpower Status and Issue Log views all share this one
    check with the same message — pulled into a decorator instead of
    hand-repeating `if not can_see_delivery(...): raise PermissionDenied(...)`
    at the top of every one of them, which is easy to forget or reword
    slightly differently at any single call site.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not can_see_delivery(request.user):
            raise PermissionDenied('The Project Management department is not open to your role.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def update_access_required(message):
    """Gate a view behind the narrower can_update_progress, with a message
    specific to what that view lets someone change."""
    def _decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not can_update_progress(request.user):
                raise PermissionDenied(message)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return _decorator


def can_manage_manpower_costing(user):
    """Who may add/edit/remove Project Manpower Costing rows.

    Deliberately narrower than can_update_progress: Site Manager has reason
    to move a delivery progress figure, but redrafting a bid-stage staffing
    cost estimate is the Project Manager's call, plus the usual
    super_admin/admin override.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(
        user.is_super_admin_user
        or user.is_admin_user
        or user.is_project_manager_user
    )


def _visible_projects(user):
    """Delivery projects this user may see, with the milestone tree attached.

    Scoping reuses `dashboard.views.projects_visible_to` rather than growing a
    third copy of the same ladder — that function exists because two copies
    had already drifted apart.
    """
    return (
        projects_visible_to(user)
        .select_related('region', 'status', 'finance')
        .prefetch_related(
            Prefetch('milestones',
                     queryset=ProjectMilestone.objects.prefetch_related('progress_entries')),
            'purchase_orders',
            'finance__milestones',
        )
    )


@login_required
def board(request):
    """Every project on one page, every column derived.

    Replaces the Projects Overview sheet, whose totals summed rows 7–13 while
    the data ran to row 17. There is no range here to get wrong.
    """
    if not can_see_delivery(request.user):
        raise PermissionDenied('The Project Management department is not open to your role.')

    projects = list(_visible_projects(request.user))
    rows = board_rows(projects)
    total_in = sum((r['cash_in'] for r in rows), ZERO)
    total_out = sum((r['cash_out'] for r in rows), ZERO)

    return render(request, 'pmo/board.html', {
        'rows': rows,
        'total_cash_in': total_in,
        'total_cash_out': total_out,
        'total_net': total_in - total_out,
        'problem_count': sum(1 for r in rows if r['weightage_problems']),
        'can_update': can_update_progress(request.user),
    })


@login_required
def project_detail(request, pk):
    """One project's WBS, parents with their children underneath."""
    if not can_see_delivery(request.user):
        raise PermissionDenied('The Project Management department is not open to your role.')

    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    rows = list(project.milestones.all())
    children = {}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row)

    # Flattened for the template: a parent immediately followed by its own
    # children, so the grid is one <tbody> and keyboard navigation runs down it
    # in the order somebody reads.
    #
    # Weights are summed here rather than read from the model properties. Those
    # walk `self.children` per row, which is a query each — fine for one row on
    # a page, but this page is the whole tree.
    display = []
    for parent in sorted((r for r in rows if r.parent_id is None), key=lambda r: r.order):
        kids = sorted(children.get(parent.pk, []), key=lambda r: r.order)
        display.append({
            'row': parent,
            'is_parent': True,
            'number': str(parent.order),
            'weightage': sum((k.weightage for k in kids), ZERO) if kids else parent.weightage,
            'completed_weightage': sum(
                (k.weightage * k.completed_fraction for k in kids), ZERO),
        })
        for child in kids:
            display.append({
                'row': child,
                'is_parent': False,
                'number': f'{parent.order}.{child.order}',
                'weightage': child.weightage,
                'completed_weightage': child.weightage * child.completed_fraction,
            })

    completion = project_completion(project)
    return render(request, 'pmo/project_detail.html', {
        'project': project,
        'display': display,
        'completion': completion,
        'completion_pct': completion * Decimal('100'),
        'problems': validate_weightages(project),
        'leaf_count': len(leaves(project)),
        'can_update': can_update_progress(request.user),
    })


@require_POST
@login_required
def update_progress(request, pk):
    """Set one milestone's completed fraction. Called per cell by the grid.

    Every change is appended to the progress log as well as written to the
    row, which is what makes "when was this last updated" answerable — the
    workbook's version of that column was TODAY() and always read as today.
    """
    if not can_update_progress(request.user):
        return JsonResponse({'error': 'You cannot update delivery progress.'}, status=403)

    milestone = get_object_or_404(
        ProjectMilestone.objects.select_related('project'), pk=pk)
    if not _visible_projects(request.user).filter(pk=milestone.project_id).exists():
        return JsonResponse({'error': 'Project not found.'}, status=404)

    # Progress belongs to the activities that carry weight. Accepting it on a
    # summary row would let a parent and its children both claim the same
    # weight, and the project would read as more complete than it is.
    if milestone.children.exists():
        return JsonResponse(
            {'error': 'This is a summary row — update the activities under it.'},
            status=400)

    raw = (request.POST.get('completed_fraction') or '').strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return JsonResponse({'error': f'"{raw}" is not a number.'}, status=400)
    if value < ZERO or value > ONE:
        return JsonResponse(
            {'error': 'Progress is a fraction between 0 and 1.'}, status=400)

    milestone.completed_fraction = value
    milestone.save(update_fields=['completed_fraction', 'updated_at'])
    MilestoneProgressEntry.objects.create(
        milestone=milestone, completed_fraction=value,
        note=(request.POST.get('note') or '').strip(),
        recorded_by=request.user)

    project = milestone.project
    return JsonResponse({
        'ok': True,
        'completed_fraction': str(value),
        'completed_weightage': str(milestone.weightage * value),
        'project_completion_pct': str(project_completion(project) * Decimal('100')),
    })


# ── importing the workbook from the browser ─────────────────────────────────
#
# The management command that does this cannot be run in production: the web
# service runs on a plan with no shell at all. So the one-off that actually
# matters — loading ten milestone sheets — has to be doable from a screen, the
# same way finance publishes a chart revision at /accounting/chart/import/.

MAX_WORKBOOK_BYTES = 15 * 1024 * 1024
PREVIEW_SALT = 'pmo.milestone_import'
PREVIEW_MAX_AGE = 60 * 60


def can_import_milestones(user):
    """Importing rewrites whole projects' WBS, so it sits above the weekly
    update rather than beside it: a project manager moves a figure, an
    administrator replaces the structure."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.is_super_admin_user or user.is_admin_user)


@login_required
def milestone_import(request):
    """Upload the projects overview workbook, see what it would do, then apply.

    The preview is the whole safety story. Sheets are matched on the Leap
    reference and an unmatched one is reported rather than guessed, so what a
    person has to check before confirming is exactly what this page shows:
    which sheet became which project, which projects already have milestones,
    and which weights do not add up.

    The parsed activities travel to the confirm step in a signed hidden field.
    Re-reading the upload on confirm would mean applying something other than
    what was previewed, which is the bug the chart importer had.
    """
    if not can_import_milestones(request.user):
        raise PermissionDenied('Importing milestones is limited to administrators.')

    if request.method != 'POST':
        return render(request, 'pmo/milestone_import.html', {})

    upload = request.FILES.get('workbook')
    if not upload:
        messages.error(request, 'Choose a workbook to upload.')
        return redirect('pmo:milestone_import')
    if upload.size > MAX_WORKBOOK_BYTES:
        messages.error(request, 'That file is larger than 15 MB, which is almost '
                                'certainly not the projects overview workbook.')
        return redirect('pmo:milestone_import')

    try:
        import openpyxl
        workbook = openpyxl.load_workbook(
            BytesIO(upload.read()), data_only=True, read_only=True)
    except Exception:
        # Deliberately broad: openpyxl raises several unrelated exception types
        # for a file that simply is not a workbook, and every one of them means
        # the same thing to somebody who picked the wrong file.
        messages.error(request, 'That file could not be read as an .xlsx workbook.')
        return redirect('pmo:milestone_import')

    results = plan_workbook(workbook, list(Project.objects.all()))
    if not results:
        messages.error(request, 'No milestone sheets found in that workbook. The '
                                'importer looks for sheets named after a Leap '
                                'reference, such as "LNA-2308-Milestones(MASCO)".')
        return redirect('pmo:milestone_import')

    for result in results:
        project = result['project']
        result['existing'] = project.milestones.count() if project else 0

    # Only matched sheets can be applied, so only those are signed. A sheet
    # with no project is a question for a person, not a thing to carry forward.
    payload = [{
        'project_id': r['project'].pk,
        'sheet': r['sheet'],
        'activities': serialise_activities(r['activities']),
    } for r in results if r['project'] and r['activities']]

    return render(request, 'pmo/milestone_import.html', {
        'results': results,
        'filename': upload.name,
        'matched': [r for r in results if r['project']],
        'unmatched': [r for r in results if not r['project']],
        'with_problems': [r for r in results if r['problems']],
        'already_have': [r for r in results if r['existing']],
        'payload': signing.dumps(payload, salt=PREVIEW_SALT, compress=True),
    })


@require_POST
@login_required
def milestone_import_apply(request):
    """Write the milestones that were previewed."""
    if not can_import_milestones(request.user):
        raise PermissionDenied('Importing milestones is limited to administrators.')

    raw = request.POST.get('payload') or ''
    if not raw:
        messages.error(request, 'Nothing to apply — upload the workbook first.')
        return redirect('pmo:milestone_import')

    try:
        payload = signing.loads(raw, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except signing.SignatureExpired:
        messages.error(request, 'That preview is more than an hour old and the '
                                'projects may have moved on since. Please upload '
                                'the workbook again.')
        return redirect('pmo:milestone_import')
    except signing.BadSignature:
        messages.error(request, 'That preview could not be verified. Please upload '
                                'the workbook again.')
        return redirect('pmo:milestone_import')

    replace = request.POST.get('replace') == 'on'
    written = skipped = 0
    # One transaction: a half-written import would leave projects with a
    # partial WBS whose weights cannot add up, which reads as a data problem
    # rather than as an import that failed.
    with transaction.atomic():
        for entry in payload:
            project = Project.objects.filter(pk=entry['project_id']).first()
            if project is None:
                continue                      # deleted since the preview
            if project.milestones.exists():
                if not replace:
                    skipped += 1
                    continue
                project.milestones.all().delete()
            written += write_activities(
                project, deserialise_activities(entry['activities']))

    messages.success(request, f'Imported {written} milestone rows.')
    if skipped:
        messages.warning(
            request,
            f'{skipped} project(s) already had milestones and were left alone. '
            f'Tick "replace existing milestones" to overwrite them.')
    return redirect('pmo:board')


# ── PM Dashboard: client PO, unpriced BOQ, responsibility & communication ──
#
# A second, client-facing view onto the same projects `board` covers —
# gated the same way (can_see_delivery) since it's the same Project
# Management audience, just looking at different information about the
# same projects.

def _pm_visible_project_or_404(request, pk):
    return get_object_or_404(_visible_projects(request.user), pk=pk)


@login_required
@delivery_required
def pm_dashboard_index(request):
    """Portfolio landing page: graphical overview of every delivery project
    this user can see, then the searchable table to pick one and drill in."""
    projects_qs = _visible_projects(request.user)

    q = (request.GET.get('q') or '').strip()
    filtered = projects_qs
    if q:
        filtered = filtered.filter(
            Q(project_name__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(customer__icontains=q)
            | Q(po_number__icontains=q)
        )
    filtered = filtered.order_by('-id')
    rows = board_rows(list(filtered))

    total_projects = projects_qs.count()
    total_value = projects_qs.aggregate(v=Sum('estimated_value'))['v'] or 0
    po_missing = projects_qs.filter(Q(po_number='') | Q(po_number__isnull=True)).count()

    status_counts = list(
        projects_qs.exclude(status__isnull=True)
        .values('status__name', 'status__color')
        .annotate(count=Count('id')).order_by('-count')
    )
    region_counts = list(
        projects_qs.exclude(region__isnull=True)
        .values('region__code')
        .annotate(count=Count('id')).order_by('-count')
    )

    milestone_buckets = {'success': 0, 'warning': 0, 'danger': 0, '': 0}
    for p in projects_qs:
        key = p.milestone_overall_status or ''
        milestone_buckets[key] = milestone_buckets.get(key, 0) + 1

    return render(request, 'pmo/pm_dashboard_index.html', {
        'rows': rows,
        'q': q,
        'total_projects': total_projects,
        'total_value': total_value,
        'po_missing': po_missing,
        'milestone_on_track': milestone_buckets.get('success', 0) + milestone_buckets.get('warning', 0),
        'milestone_at_risk': milestone_buckets.get('danger', 0),
        'milestone_no_data': milestone_buckets.get('', 0),
        'status_labels_json': json.dumps([s['status__name'] or 'Unknown' for s in status_counts]),
        'status_data_json': json.dumps([s['count'] for s in status_counts]),
        'status_colors_json': json.dumps([s['status__color'] or '#6c757d' for s in status_counts]),
        'region_labels_json': json.dumps([r['region__code'] or 'Unknown' for r in region_counts]),
        'region_data_json': json.dumps([r['count'] for r in region_counts]),
    })


def _style_export_header_and_grid(ws, header_idx, ncols):
    """Shared openpyxl look for this module's Excel exports: a dark header
    row, a thin grey border on every cell from the header down, and the
    header row frozen. Pulled into one place so pm_dashboard_export_excel
    and _build_issue_log_workbook don't each hand-declare the same
    PatternFill/Font/Border boilerplate."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    header_fill = PatternFill('solid', fgColor='2C3E50')
    header_font = Font(bold=True, color='FFFFFF', size=10)
    thin = Side(style='thin', color='D5D5D5')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for c in ws[header_idx]:
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    for row in ws.iter_rows(min_row=header_idx, max_row=ws.max_row, min_col=1, max_col=ncols):
        for c in row:
            c.border = border
    ws.freeze_panes = ws.cell(row=header_idx + 1, column=1)


@login_required
@delivery_required
def pm_dashboard_export_excel(request):
    """The PM Dashboard's project list as an .xlsx, one row per project, in
    the same column order as the PM's own tracking workbook. Honours the
    same search box as the page it's exported from."""
    import openpyxl
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    projects_qs = _visible_projects(request.user)
    q = (request.GET.get('q') or '').strip()
    if q:
        projects_qs = projects_qs.filter(
            Q(project_name__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(customer__icontains=q)
            | Q(po_number__icontains=q)
        )
    rows = board_rows(list(projects_qs.order_by('-id')))

    columns = [
        'Project Name', 'Expected Completion Date', 'Weightage of Pending Activities',
        'Completed Activity Weightage', 'Planned Project End Date', 'Delay (Months)',
        'Cash In (SAR)', 'Achieved Milestones', 'No. Pending Milestones', 'Action By',
        'Project Status',
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'PM Dashboard'
    ncols = len(columns)

    delay_fill = PatternFill('solid', fgColor='FFC7CE')
    green_font = Font(bold=True, color='198754')
    red_font = Font(bold=True, color='DC3545')

    ws.append(['PM Dashboard — Project Status'])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws['A1'].font = Font(bold=True, size=14, color='404040')
    generated = timezone.localtime(timezone.now()).strftime('%d %b %Y at %H:%M')
    ws.append([f'Generated {generated}'])
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    ws['A2'].font = Font(italic=True, size=9, color='666666')
    ws.append([])

    header_idx = ws.max_row + 1
    ws.append(columns)

    for row in rows:
        project = row['project']
        pending_pct = float(100 - row['completion_pct'])
        completed_pct = float(row['completion_pct'])
        ws.append([
            project.project_name,
            row['expected_completion_date'],
            pending_pct / 100,
            completed_pct / 100,
            row['end_date'],
            row['delay_months'] or 0,
            float(row['cash_in']),
            row['achieved_milestones'],
            row['pending_milestones'],
            row['action_by'],
            project.status.name if project.status else '',
        ])
        r = ws.max_row
        ws.cell(row=r, column=2).number_format = 'dd mmm yyyy'
        ws.cell(row=r, column=3).number_format = '0.0%'
        ws.cell(row=r, column=4).number_format = '0.0%'
        ws.cell(row=r, column=5).number_format = 'dd mmm yyyy'
        ws.cell(row=r, column=7).number_format = '#,##0.00'

        # Just the Status cell gets its own colour, same as the small badge
        # it renders as everywhere else in the ERP — a whole-row fill turned
        # into a rainbow once real data (10+ distinct status colours) hit it.
        if project.status and project.status.color:
            hex_color = project.status.color.lstrip('#').upper()
            if len(hex_color) == 6:
                status_cell = ws.cell(row=r, column=11)
                status_cell.fill = PatternFill('solid', fgColor=hex_color)
                status_cell.font = Font(bold=True, color='FFFFFF')

        if (row['delay_months'] or 0) > 0:
            ws.cell(row=r, column=6).fill = delay_fill
        ws.cell(row=r, column=8).font = green_font   # Achieved Milestones
        ws.cell(row=r, column=9).font = red_font     # No. Pending Milestones

    # Weightage of Pending Activities / Completed Activity Weightage: a real
    # Excel colour-scale rule rather than a fixed per-cell fill, so the
    # shading is a genuine gradient across whatever values are in the sheet
    # (and stays correct if someone edits it afterwards).
    if len(rows) > 0:
        first_data_row = header_idx + 1
        last_data_row = ws.max_row
        ws.conditional_formatting.add(
            f'C{first_data_row}:C{last_data_row}',
            ColorScaleRule(start_type='min', start_color='FFFFFF',
                           end_type='max', end_color='C41E3A'))
        ws.conditional_formatting.add(
            f'D{first_data_row}:D{last_data_row}',
            ColorScaleRule(start_type='min', start_color='FFFFFF',
                           end_type='max', end_color='198754'))

    widths = [40, 20, 16, 16, 20, 12, 16, 14, 16, 18, 16]
    for i, w in enumerate(widths[:ncols], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _style_export_header_and_grid(ws, header_idx, ncols)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="PM_Dashboard.xlsx"'
    return response


@login_required
@delivery_required
def pm_project_overview(request, pk):
    """Graphical snapshot of one project — status tiles + a milestone chart,
    then cards linking into the dedicated PO / BOQ / matrix pages."""
    project = _pm_visible_project_or_404(request, pk)
    po_document_count = project.documents.filter(document_type='po_document').count()
    costing_sheet_count = project.costing_sheets.count()
    responsibility_matrix = getattr(project, 'responsibility_matrix', None)
    communication_matrix = getattr(project, 'communication_matrix', None)
    has_milestones = project.milestones.exists()
    delivery = board_row(project) if has_milestones else None
    checklist = milestone_checklist(project) if has_milestones else []
    return render(request, 'pmo/pm_project_overview.html', {
        'project': project,
        'po_document_count': po_document_count,
        'costing_sheet_count': costing_sheet_count,
        'responsibility_row_count': len(responsibility_matrix.rows) if responsibility_matrix else 0,
        'communication_row_count': len(communication_matrix.rows) if communication_matrix else 0,
        'delivery': delivery,
        'milestone_checklist': checklist,
        'milestone_rows_json': json.dumps([
            {'label': row['label'], 'variance_display': row['variance_display']}
            for row in project.milestone_display_rows
        ]),
    })


@login_required
@delivery_required
def pm_project_po(request, pk):
    project = _pm_visible_project_or_404(request, pk)
    po_documents = project.documents.filter(document_type='po_document').order_by('-uploaded_at')
    return render(request, 'pmo/pm_project_po.html', {
        'project': project,
        'po_documents': po_documents,
    })


@login_required
@delivery_required
def pm_project_boq(request, pk):
    project = _pm_visible_project_or_404(request, pk)
    costing_sheets = project.costing_sheets.all().order_by('-updated_at')
    return render(request, 'pmo/pm_project_boq.html', {
        'project': project,
        'costing_sheets': costing_sheets,
    })


def _matrix_detail(request, pk, related_name, default_columns, template):
    """Shared by pm_responsibility_matrix and pm_communication_matrix — the
    two grids differ only in which relation/defaults they read."""
    project = _pm_visible_project_or_404(request, pk)
    matrix = getattr(project, related_name, None)
    return render(request, template, {
        'project': project,
        'columns': matrix.columns if matrix else default_columns(),
        'rows': matrix.rows if matrix else [],
    })


@login_required
@delivery_required
def pm_responsibility_matrix(request, pk):
    return _matrix_detail(request, pk, 'responsibility_matrix',
                           default_responsibility_columns, 'pmo/pm_responsibility_matrix.html')


@login_required
@delivery_required
def pm_communication_matrix(request, pk):
    return _matrix_detail(request, pk, 'communication_matrix',
                           default_communication_columns, 'pmo/pm_communication_matrix.html')


def _matrix_edit(request, pk, model, related_name, default_columns, matrix_title,
                  success_message, view_url_name):
    """Shared by pm_responsibility_matrix_edit and pm_communication_matrix_edit
    — same get/parse/sanitize/save shape either way, differing only in which
    model and defaults back it. `matrix` is built in memory rather than
    fetched-or-created: opening this page is a GET and must not itself write
    a blank matrix row."""
    project = _pm_visible_project_or_404(request, pk)
    matrix = getattr(project, related_name, None) or model(project=project)
    if request.method == 'POST':
        try:
            columns_raw = json.loads(request.POST.get('columns_json') or '[]')
            rows_raw = json.loads(request.POST.get('rows_json') or '[]')
        except (ValueError, TypeError):
            columns_raw, rows_raw = [], []
        matrix.columns, matrix.rows = sanitize_grid(columns_raw, rows_raw, default_columns())
        matrix.updated_by = request.user
        matrix.save()
        messages.success(request, success_message)
        return redirect(view_url_name, pk=project.pk)
    return render(request, 'pmo/pm_matrix_edit.html', {
        'project': project,
        'matrix_title': matrix_title,
        'cancel_url_name': view_url_name,
        'columns_json': json.dumps(matrix.columns or default_columns()),
        'rows_json': json.dumps(matrix.rows or []),
    })


@login_required
@delivery_required
def pm_responsibility_matrix_edit(request, pk):
    return _matrix_edit(request, pk, ResponsibilityMatrix, 'responsibility_matrix',
                         default_responsibility_columns, 'Responsibility Matrix',
                         'Responsibility matrix updated.', 'pmo:pm_responsibility_matrix')


@login_required
@delivery_required
def pm_communication_matrix_edit(request, pk):
    return _matrix_edit(request, pk, CommunicationMatrix, 'communication_matrix',
                         default_communication_columns, 'Communication Matrix',
                         'Communication matrix updated.', 'pmo:pm_communication_matrix')


@login_required
@delivery_required
def pm_communication_matrix_export_pdf(request, pk):
    """Client-facing PDF report of the communication matrix — plain table,
    no pricing/internal data, safe to share externally."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    project = _pm_visible_project_or_404(request, pk)
    # Read-only export — never create a row just because someone viewed it.
    matrix = getattr(project, 'communication_matrix', None)
    columns = matrix.columns if matrix else default_communication_columns()
    rows = matrix.rows if matrix else []

    base = getSampleStyleSheet()
    st_title = ParagraphStyle('t', parent=base['Title'], fontName='Helvetica-Bold',
                               fontSize=16, leading=20, textColor=colors.HexColor('#1A1A1A'))
    st_meta = ParagraphStyle('m', parent=base['Normal'], fontName='Helvetica',
                              fontSize=8.5, leading=12, textColor=colors.HexColor('#6C757D'))
    st_head = ParagraphStyle('h', parent=base['Normal'], fontName='Helvetica-Bold',
                              fontSize=8, leading=10, textColor=colors.white)
    st_cell = ParagraphStyle('c', parent=base['Normal'], fontSize=8, leading=10.5)

    buf = BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(
        buf, pagesize=page, topMargin=13 * mm, bottomMargin=13 * mm,
        leftMargin=12 * mm, rightMargin=12 * mm,
        title=f'Communication Matrix - {project.project_name}')
    content_w = page[0] - 24 * mm

    story = [
        Paragraph('Communication Matrix', st_title),
        Spacer(1, 2 * mm),
        Paragraph(f'{project.project_name} &middot; exported {timezone.localtime():%d %B %Y}', st_meta),
        Spacer(1, 5 * mm),
    ]

    col_count = max(len(columns), 1)
    col_width = content_w / col_count
    header = [Paragraph(col.get('name') or '', st_head) for col in columns]
    table_data = [header]
    for row in rows:
        cells = (row or {}).get('cells', {})
        table_data.append([
            Paragraph((cells.get(col['key'], {}) or {}).get('text', '') or '', st_cell)
            for col in columns
        ])
    if len(table_data) == 1:
        table_data.append([Paragraph('&mdash;', st_cell) for _ in columns])

    table = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#C41E3A')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CCCCCC')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F7F7F7')]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(table)

    doc.build(story)
    buf.seek(0)
    response = HttpResponse(buf.read(), content_type='application/pdf')
    filename = f"communication-matrix-{project.serial_number or project.pk}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ── Manpower Status ─────────────────────────────────────────────────────────
#
# Every HR employee, office or site, with the extra fields Project
# Management tracks about them filled in where someone has entered them.
# Viewing follows the same audience as the rest of this department
# (can_see_delivery); filling in/clearing details or onboarding someone
# brand new follows the narrower can_update_progress ladder, same as moving
# a milestone figure.

@login_required
@delivery_required
def manpower_list(request):
    from hr.models import Employee

    employees = Employee.objects.select_related('manpower_resource').order_by('full_name')
    q = (request.GET.get('q') or '').strip()
    if q:
        employees = employees.filter(
            Q(full_name__icontains=q)
            | Q(iqama_number__icontains=q)
            | Q(designation__icontains=q)
        )
    return render(request, 'pmo/manpower_list.html', {
        'employees': employees,
        'q': q,
        'can_manage': can_update_progress(request.user),
    })


@login_required
@update_access_required('Only Project Management can add employees.')
def manpower_create(request):
    """Onboard someone who isn't in HR at all yet."""
    if request.method == 'POST':
        form = NewEmployeeManpowerForm(request.POST)
        if form.is_valid():
            resource = form.save(created_by=request.user)
            messages.success(request, f'{resource.employee.full_name} added.')
            return redirect('pmo:manpower_list')
    else:
        form = NewEmployeeManpowerForm()
    return render(request, 'pmo/manpower_new_employee_form.html', {'form': form})


@login_required
@update_access_required('Only Project Management can edit manpower details.')
def manpower_edit(request, employee_pk):
    """Fill in / update the Project-Management fields for one employee who
    already exists in HR."""
    from hr.models import Employee

    employee = get_object_or_404(Employee, pk=employee_pk)
    # Built in memory, not fetched-or-created: opening this page is a GET and
    # must not itself write a row — only an actual save should. Same pattern
    # as the responsibility/communication matrix edit views above.
    resource = getattr(employee, 'manpower_resource', None) or ManpowerResource(employee=employee)
    if request.method == 'POST':
        form = ManpowerDetailsForm(request.POST, instance=resource)
        if form.is_valid():
            form.save()
            messages.success(request, f'{employee.full_name} updated.')
            return redirect('pmo:manpower_list')
    else:
        form = ManpowerDetailsForm(instance=resource)
    return render(request, 'pmo/manpower_details_form.html', {
        'form': form, 'employee': employee,
    })


@require_POST
@login_required
@update_access_required('Only Project Management can clear manpower details.')
def manpower_clear(request, employee_pk):
    """Reset an employee's Project-Management fields back to blank — they
    stay on the list (they're still a real HR employee), just without any
    of the extra details filled in."""
    resource = get_object_or_404(ManpowerResource, employee_id=employee_pk)
    resource.delete()
    messages.success(request, 'Details cleared.')
    return redirect('pmo:manpower_list')


# ── Issue Log ─────────────────────────────────────────────────────────────
#
# The delivery team's risk/issue register, browsed and exported one month
# at a time by date_identified — same "?year=&month=" paging and per-month
# Excel export (plus a zip of every month) as engineer_calendar's calendar.

def _resolve_year_month(request):
    """Read year/month from the query string, falling back to today when
    absent or invalid — same rule engineer_calendar's month paging uses."""
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
        if not (1 <= month <= 12):
            raise ValueError('Month out of range')
        calendar.monthrange(year, month)
    except (ValueError, TypeError, OverflowError):
        year, month = today.year, today.month
    return year, month


def _adjacent_month(year, month, delta):
    """delta=-1 for the previous month, +1 for the next, wrapping the year."""
    month += delta
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1
    return year, month


def _build_issue_log_workbook(year, month, visible_projects):
    """Build one month's Issue Log workbook. Pulled out of the export view so
    the all-months zip can reuse it without duplicating the styling.

    `visible_projects` scopes the issues to what this user may see — the
    same region/ownership ladder as everywhere else in pmo — so an export
    can't be used to pull issues for a project someone couldn't otherwise
    reach."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    issues = (ProjectIssue.objects
              .filter(date_identified__year=year, date_identified__month=month,
                      project__in=visible_projects)
              .select_related('project', 'logged_by')
              .order_by('date_identified', 'pk'))

    columns = [
        'ID', 'Status', 'Priority', 'Description', 'Project Name', 'Owner',
        'Estimated Resolution Date', 'Escalation Needed (Y/N)?', 'Impact', 'Actions',
        'Date Identified', 'Logged By', 'Actual Resolution/Completion Date',
        'Final Resolution & Follow-on Actions',
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = calendar.month_abbr[month].upper()
    ncols = len(columns)

    wrap = Alignment(vertical='top', wrap_text=True)

    # Severity legend, same as the top of the source workbook.
    ws.append(['Issue Severity Description'])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws['A1'].font = Font(bold=True, size=12)
    legend = [
        (ProjectIssue.PRIORITY_CRITICAL, 'Issue will stop project progress.'),
        (ProjectIssue.PRIORITY_HIGH, 'Issue will likely impact budget, schedule or scope.'),
        (ProjectIssue.PRIORITY_MEDIUM, 'Issue impacts the project, but could be mitigated to '
                                       'avoid an impact on budget, schedule or scope.'),
        (ProjectIssue.PRIORITY_LOW, 'Issue is low impact and/or low effort to resolve.'),
    ]
    priority_labels = dict(ProjectIssue.PRIORITY_CHOICES)
    for key, desc in legend:
        ws.append([priority_labels[key], desc])
        r = ws.max_row
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=ncols)
        ws.cell(row=r, column=1).fill = PatternFill('solid', fgColor=ProjectIssue.PRIORITY_COLORS[key])
        ws.cell(row=r, column=1).font = Font(bold=True, color='FFFFFF')
    ws.append([])

    header_idx = ws.max_row + 1
    ws.append(columns)

    for issue in issues:
        ws.append([
            issue.pk,
            issue.get_status_display(),
            issue.get_priority_display(),
            issue.description,
            issue.project.project_name,
            issue.owner,
            issue.estimated_resolution_date,
            'Y' if issue.escalation_needed else 'N',
            issue.impact,
            issue.actions,
            issue.date_identified,
            issue.logged_by.get_full_name() if issue.logged_by else '',
            issue.actual_resolution_date,
            issue.final_resolution,
        ])
        r = ws.max_row
        for col_idx in (7, 11, 13):
            ws.cell(row=r, column=col_idx).number_format = 'dd mmm yyyy'
        for c in ws[r]:
            c.alignment = wrap
        # Priority colours the whole cell (matching the legend); Status text
        # is green once Closed, the brand red while still Open.
        ws.cell(row=r, column=3).fill = PatternFill('solid', fgColor=issue.priority_color)
        ws.cell(row=r, column=3).font = Font(bold=True, color='FFFFFF')
        status_color = '198754' if issue.status == ProjectIssue.STATUS_CLOSED else 'C41E3A'
        ws.cell(row=r, column=2).font = Font(bold=True, color=status_color)

    widths = [6, 10, 10, 40, 32, 14, 18, 14, 30, 30, 15, 16, 20, 40]
    for i, w in enumerate(widths[:ncols], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _style_export_header_and_grid(ws, header_idx, ncols)

    filename = f'Issue_Log_{calendar.month_name[month]}_{year}.xlsx'
    return wb, filename


@login_required
@delivery_required
def issue_log_list(request):
    year, month = _resolve_year_month(request)
    issues = (ProjectIssue.objects
              .filter(date_identified__year=year, date_identified__month=month,
                      project__in=projects_visible_to(request.user))
              .select_related('project', 'logged_by'))
    prev_year, prev_month = _adjacent_month(year, month, -1)
    next_year, next_month = _adjacent_month(year, month, 1)

    return render(request, 'pmo/issue_log_list.html', {
        'issues': issues,
        'year': year,
        'month': month,
        'month_name': calendar.month_name[month],
        'prev_year': prev_year, 'prev_month': prev_month,
        'next_year': next_year, 'next_month': next_month,
        'can_manage': can_update_progress(request.user),
    })


@login_required
@update_access_required('Only Project Management can log issues.')
def issue_log_create(request):
    if request.method == 'POST':
        form = ProjectIssueForm(request.POST, user=request.user)
        if form.is_valid():
            issue = form.save(commit=False)
            issue.logged_by = request.user
            issue.save()
            messages.success(request, 'Issue logged.')
            return redirect(
                f"{reverse('pmo:issue_log_list')}?year={issue.date_identified.year}&month={issue.date_identified.month}")
    else:
        form = ProjectIssueForm(user=request.user, initial={'date_identified': timezone.localdate()})
    return render(request, 'pmo/issue_log_form.html', {'form': form, 'is_new': True})


@login_required
@update_access_required('Only Project Management can edit issues.')
def issue_log_edit(request, pk):
    issue = get_object_or_404(
        ProjectIssue.objects.filter(project__in=projects_visible_to(request.user)), pk=pk)
    if request.method == 'POST':
        form = ProjectIssueForm(request.POST, instance=issue, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Issue updated.')
            return redirect(
                f"{reverse('pmo:issue_log_list')}?year={issue.date_identified.year}&month={issue.date_identified.month}")
    else:
        form = ProjectIssueForm(instance=issue, user=request.user)
    return render(request, 'pmo/issue_log_form.html', {
        'form': form, 'is_new': False, 'issue': issue,
    })


@login_required
@delivery_required
def fault_loss_list(request):
    """All logged faults, losses and delays across the projects this user
    can see - the digital equivalent of the Faults & Losses Prevention
    tracking sheet, one row per project system rather than per month."""
    entries = (FaultLossEntry.objects
               .filter(project__in=projects_visible_to(request.user))
               .select_related('project', 'created_by'))
    return render(request, 'pmo/fault_loss_list.html', {
        'entries': entries,
        'can_manage': can_update_progress(request.user),
    })


@login_required
@update_access_required('Only Project Management can log faults and losses.')
def fault_loss_create(request):
    if request.method == 'POST':
        form = FaultLossEntryForm(request.POST, user=request.user)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by = request.user
            entry.save()
            messages.success(request, 'Fault/loss entry logged.')
            return redirect('pmo:fault_loss_list')
    else:
        form = FaultLossEntryForm(user=request.user)
    project_refs = {p.pk: p.proposal_reference for p in form.fields['project'].queryset}
    return render(request, 'pmo/fault_loss_form.html', {
        'form': form, 'is_new': True, 'project_refs': project_refs,
    })


@login_required
@update_access_required('Only Project Management can edit fault/loss entries.')
def fault_loss_edit(request, pk):
    entry = get_object_or_404(
        FaultLossEntry.objects.filter(project__in=projects_visible_to(request.user)), pk=pk)
    if request.method == 'POST':
        form = FaultLossEntryForm(request.POST, instance=entry, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Fault/loss entry updated.')
            return redirect('pmo:fault_loss_list')
    else:
        form = FaultLossEntryForm(instance=entry, user=request.user)
    project_refs = {p.pk: p.proposal_reference for p in form.fields['project'].queryset}
    return render(request, 'pmo/fault_loss_form.html', {
        'form': form, 'is_new': False, 'entry': entry, 'project_refs': project_refs,
    })


def _build_fault_loss_workbook(visible_projects):
    """Build the Faults & Losses Prevention workbook - every logged entry
    across the projects this user may see, in the source sheet's own
    column order. Not month-scoped like the Issue Log export: this tab
    has no date-driven grouping, so one workbook covers everything."""
    import openpyxl
    from openpyxl.styles import Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    entries = (FaultLossEntry.objects
               .filter(project__in=visible_projects)
               .select_related('project', 'created_by')
               .order_by('project__project_name', 'pk'))

    columns = [
        'Project', 'System', 'LNA Ref#', 'PO#', 'Location',
        'Delivery Time Impact', 'Duration of Delay (days)', 'Cost Impact',
        'Amount of Cost (SAR)', 'Change Order Required', 'Root Causes/Faults',
        'Deviation Category', 'Responsibility (Departments)', 'Corrective Action',
        'Corrective Action By (Departments)', 'Status', 'Closed On', 'Latest Update',
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Faults & Losses'
    ncols = len(columns)
    wrap = Alignment(vertical='top', wrap_text=True)

    header_idx = 1
    ws.append(columns)

    # Same hex pair as the on-screen .fl-pink/.fl-green classes, so the
    # export and the page never show a different colour for the same cell.
    PINK = 'F8D7DA'
    GREEN = 'D1E7DD'

    for entry in entries:
        ws.append([
            entry.project.project_name,
            entry.system,
            entry.lna_ref,
            entry.po_number,
            entry.location,
            'Yes' if entry.delivery_time_impact else 'No',
            entry.delay_days,
            'Yes' if entry.cost_impact else 'No',
            entry.cost_amount,
            'Yes' if entry.change_order_required else 'No',
            entry.root_causes,
            entry.get_deviation_category_display(),
            entry.responsibility_departments,
            entry.corrective_action,
            entry.corrective_action_by,
            entry.get_status_display(),
            entry.closed_on,
            entry.latest_update,
        ])
        r = ws.max_row
        for c in ws[r]:
            c.alignment = wrap
        for col_idx in (17, 18):
            ws.cell(row=r, column=col_idx).number_format = 'dd mmm yyyy'
        # Same colour rules as the on-screen table.
        ws.cell(row=r, column=6).fill = PatternFill(
            'solid', fgColor=PINK if entry.delivery_time_impact else GREEN)
        ws.cell(row=r, column=7).fill = PatternFill(
            'solid', fgColor=PINK if entry.delay_days > 0 else GREEN)
        ws.cell(row=r, column=8).fill = PatternFill(
            'solid', fgColor=PINK if entry.cost_impact else GREEN)
        if entry.cost_amount is not None:
            ws.cell(row=r, column=9).fill = PatternFill(
                'solid', fgColor=PINK if entry.cost_amount > 0 else GREEN)
        ws.cell(row=r, column=10).fill = PatternFill(
            'solid', fgColor=PINK if entry.change_order_required else GREEN)
        if entry.deviation_category:
            ws.cell(row=r, column=12).fill = PatternFill('solid', fgColor=entry.deviation_color)

    widths = [28, 20, 16, 14, 14, 12, 12, 10, 14, 12, 40, 14, 24, 36, 24, 10, 14, 14]
    for i, w in enumerate(widths[:ncols], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    _style_export_header_and_grid(ws, header_idx, ncols)

    filename = 'Faults_and_Losses_Prevention.xlsx'
    return wb, filename


@login_required
@delivery_required
def fault_loss_export_excel(request):
    wb, filename = _build_fault_loss_workbook(projects_visible_to(request.user))
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@login_required
@delivery_required
def issue_log_export_excel(request):
    year, month = _resolve_year_month(request)
    wb, filename = _build_issue_log_workbook(year, month, projects_visible_to(request.user))
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@login_required
@delivery_required
def issue_log_export_all_zip(request):
    """One zip with a full Issue Log workbook for every month that has any
    issues at all — same format as the single-month Export to Excel button."""
    visible_projects = projects_visible_to(request.user)
    months = list(
        ProjectIssue.objects.filter(project__in=visible_projects)
        .values_list('date_identified__year', 'date_identified__month')
        .distinct().order_by('-date_identified__year', '-date_identified__month')
    )
    if not months:
        messages.error(request, 'There are no logged issues to download yet.')
        return redirect('pmo:issue_log_list')

    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for yr, mo in months:
            month_wb, month_filename = _build_issue_log_workbook(yr, mo, visible_projects)
            month_buf = BytesIO()
            month_wb.save(month_buf)
            zf.writestr(month_filename, month_buf.getvalue())

    response = HttpResponse(buf.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="Issue_Log_All_Months.zip"'
    return response


# ── Project Manpower Costing ─────────────────────────────────────────────────
#
# A second, PM-facing view onto the same projects `board`/`pm_dashboard_index`
# cover — same audience (can_see_delivery) for viewing, but a narrower
# audience (can_manage_manpower_costing) for editing, since this is one
# person's bid-stage estimate, not a delivery figure the whole department
# reports against.

def _mpc_header_for(project):
    """The header row, built in memory when none exists yet — a GET must
    never write one, matching the ResponsibilityMatrix/ManpowerResource rule
    elsewhere in this file."""
    return getattr(project, 'manpower_costing_header', None) or ManpowerCostingHeader(project=project)


@login_required
@delivery_required
def mpc_index(request):
    """Portfolio landing page for Project Manpower Costing: the same project
    list pm_dashboard_index covers, without its charts — a plain searchable
    table with each project's headcount and bid-value chips."""
    projects_qs = (
        _visible_projects(request.user)
        .prefetch_related('grade_structure_lines', 'designation_manpower_lines',
                           'first_year_maintenance_lines')
        .select_related('manpower_costing_header')
    )

    q = (request.GET.get('q') or '').strip()
    filtered = projects_qs
    if q:
        filtered = filtered.filter(
            Q(project_name__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(customer__icontains=q)
            | Q(po_number__icontains=q)
        )
    filtered = filtered.order_by('-id')

    rows = []
    for project in filtered:
        header = _mpc_header_for(project)
        # Summed straight from the prefetched designation rows rather than
        # via GradeStructureLine.headcount, which would issue one query per
        # grade line per project on this portfolio-wide page — fine on a
        # single project's own page, not here.
        headcount = sum((d.headcount for d in project.designation_manpower_lines.all()), 0)
        rows.append({
            'project': project,
            'headcount': headcount,
            'total_annual_cost': header.total_annual_cost,
            'proposed_contract_value': header.proposed_contract_value,
        })

    return render(request, 'pmo/mpc_index.html', {
        'rows': rows,
        'q': q,
        'total_projects': projects_qs.count(),
        # Proposed Contract Value sits right next to Total Annual Cost on
        # this page — showing both to a viewer who can't edit the bid would
        # hand them the margin by subtraction, the same concern the review
        # raised for the project overview and First Year Maintenance pages.
        'can_edit': can_manage_manpower_costing(request.user),
    })


@login_required
@delivery_required
def mpc_project_overview(request, pk):
    """One project's Manpower Costing snapshot: the 3 bid-value stat tiles,
    then cards linking into First Year Maintenance / Grade Structure /
    Engineer Rate Table / Manpower Consolidated."""
    project = _pm_visible_project_or_404(request, pk)
    header = _mpc_header_for(project)
    return render(request, 'pmo/mpc_project_overview.html', {
        'project': project,
        'header': header,
        'can_edit': can_manage_manpower_costing(request.user),
        'grade_structure_row_count': project.grade_structure_lines.count(),
        'first_year_maintenance_row_count': project.first_year_maintenance_lines.count(),
    })


def _fym_sections(formset):
    """Group the (already-built) formset's forms by section, in the same
    order/shape the read-only view used to build by hand — one table per
    section, each carrying its own forms (for rendering inputs) and
    instances (for computing the subtotal). The subtotal is a plain sum of
    each line's own annual_contribution — section membership no longer
    decides how a line annualises, only how it's grouped on the page."""
    sections = []
    for section, label in FirstYearMaintenanceLine.SECTION_CHOICES:
        section_forms = [f for f in formset.forms if f.instance.section == section]
        section_lines = [f.instance for f in section_forms]
        subtotal = sum((line.annual_contribution for line in section_lines), Decimal('0'))
        sections.append({
            'code': section, 'label': label, 'forms': section_forms, 'subtotal': subtotal,
        })
    return sections


@login_required
@delivery_required
def mpc_first_year_detail(request, pk):
    """First Year Maintenance: one page, always showing the real data.
    A PM edits every table directly here (project header fields, all 4
    section tables) and saves once — there is no separate edit page to
    navigate to and back from, which was also how a stale back-navigation
    could show pre-save data instead of what was just added."""
    project = _pm_visible_project_or_404(request, pk)
    header = _mpc_header_for(project)
    can_edit = can_manage_manpower_costing(request.user)

    if request.method == 'POST':
        if not can_edit:
            raise PermissionDenied('Only the Project Manager can edit the First Year Maintenance sheet.')
        header_form = ManpowerCostingHeaderForm(request.POST, instance=header)
        po_form = ProjectPOBasicsForm(request.POST, instance=project)
        formset = FirstYearMaintenanceLineFormSet(request.POST, instance=project)
        if header_form.is_valid() and po_form.is_valid() and formset.is_valid():
            with transaction.atomic():
                po_form.save()
                header = header_form.save(commit=False)
                header.project = project
                header.updated_by = request.user
                header.save()
                formset.save()
            messages.success(request, 'First Year Maintenance saved.')
            return redirect('pmo:mpc_first_year_detail', pk=project.pk)
    else:
        header_form = ManpowerCostingHeaderForm(instance=header)
        po_form = ProjectPOBasicsForm(instance=project)
        formset = FirstYearMaintenanceLineFormSet(instance=project)

    response = render(request, 'pmo/mpc_first_year_detail.html', {
        'project': project,
        'header': header,
        'header_form': header_form,
        'po_form': po_form,
        'formset': formset,
        'sections': _fym_sections(formset),
        'can_edit': can_edit,
    })
    # This page is a live editing surface, not a static report — a stale
    # cached/back-navigated copy would show pre-save rows as if they were
    # current, which is exactly the confusing bug a no-store header rules out.
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@delivery_required
def mpc_first_year_export_excel(request, pk):
    """First Year Maintenance as an .xlsx replicating the source workbook's
    own look, verified cell-by-cell against the uploaded template: Leap logo
    top-left, a plain white sheet (no banner fills), Cambria font throughout,
    gray column-header labels, bold section/TOTAL titles, and the bid-value
    block's red labels + bold green figures."""
    import os

    import openpyxl
    from django.conf import settings
    from django.contrib.staticfiles import finders
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Border, Font, Side

    project = _pm_visible_project_or_404(request, pk)
    header = _mpc_header_for(project)
    formset = FirstYearMaintenanceLineFormSet(instance=project)
    sections = _fym_sections(formset)
    # Same narrowing as the HTML page's KPI tiles: a viewer who can't edit
    # the bid (e.g. Site Manager) doesn't get Proposed Contract Value/
    # Profit/VAT-inclusive value here either — an exported file is a more
    # durable leak than a page element, so it needs the same gate.
    can_see_bid_value = can_manage_manpower_costing(request.user)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'First Year Maintenance'
    for col, width in zip(
        'ABCDEFGHIJKLMNOP',
        [9, 40, 9, 40, 18, 15, 15, 17, 22, 24, 3, 3, 18, 18, 20, 18],
    ):
        ws.column_dimensions[col].width = width

    FONT = 'Cambria'
    # openpyxl silently treats a 6-digit hex colour as ARGB with a 00 (fully
    # transparent) alpha channel, not FF — every colour below MUST be the
    # full 8-digit form or it renders as invisible text.
    GRAY = 'FF808080'
    RED = 'FFFF0000'
    GREEN = 'FF00B050'
    money = '#,##0.00'
    bid_money = f'[${header.currency}]\\ #,##0.00'
    CENTER = Alignment(horizontal='center', vertical='center')
    UNDERLINE = Border(top=Side(style='thin'), bottom=Side(style='double'))
    TOTAL_BORDER = Border(top=Side(style='thin'), bottom=Side(style='medium'))

    def cell(row, col, value=None, size=10, bold=False, color=None, fmt='General',
             align=None, border=None):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(name=FONT, size=size, bold=bold, color=color)
        c.number_format = fmt
        if align:
            c.alignment = align
        if border:
            c.border = border
        return c

    def underline_row(row, last_col=10):
        for col in range(1, last_col + 1):
            ws.cell(row=row, column=col).border = UNDERLINE

    def total_border_row(row, last_col=10):
        for col in range(1, last_col + 1):
            ws.cell(row=row, column=col).border = TOTAL_BORDER

    # ── Logo (top-left, matching the source file's own anchor) ──
    logo_path = finders.find('images/leap_logo.jpg')
    if not logo_path:
        candidate = os.path.join(str(settings.BASE_DIR), 'static', 'images', 'leap_logo.jpg')
        if os.path.exists(candidate):
            logo_path = candidate
    if logo_path and os.path.exists(logo_path):
        img = XLImage(logo_path)
        img.width, img.height = 175, 60
        ws.add_image(img, 'A1')

    # ── Project / bid details (mirrors the source's A/D + G/H two-column layout) ──
    cell(6, 1, 'Client'); cell(6, 4, project.customer or '—')
    cell(6, 7, 'LNA Proposal'); cell(6, 8, project.proposal_reference or '—', color=RED)
    cell(7, 1, 'Project Name'); cell(7, 4, project.project_name)
    cell(7, 7, 'PO No.'); cell(7, 8, project.po_number or '—')
    cell(8, 7, 'PO Date')
    cell(8, 8, project.estimated_po_date.strftime('%d %B %Y') if project.estimated_po_date else 'TBA')
    cell(9, 7, 'PO Type'); cell(9, 8, header.po_type or 'TBA', color=RED)
    cell(10, 7, 'PO Value')
    cell(10, 8, float(header.po_value) if header.po_value else 'TBA',
         fmt=money if header.po_value else 'General')
    cell(11, 7, 'Currency'); cell(11, 8, header.currency)
    cell(12, 7, 'VAT %'); cell(12, 8, float(header.vat_rate) / 100, fmt='0.0%')

    r = 14

    def section_table(row, section):
        cell(row, 1, section['label'], size=10, bold=True)
        underline_row(row)
        row += 1
        for col, text in zip('FGHIJ', ['Nature of Expense', 'Amount', 'Qty', 'Annual Contribution', 'remarks']):
            cell(row, ord(col) - 64, text, size=8, color=GRAY, align=CENTER)
        underline_row(row)
        row += 1
        for i, form in enumerate(section['forms'], start=1):
            line = form.instance
            cell(row, 1, i, align=CENTER)
            cell(row, 2, line.description)
            cell(row, 6, line.get_nature_of_expense_display(), size=8, align=CENTER)
            cell(row, 7, float(line.amount), fmt=money)
            cell(row, 8, float(line.qty), align=CENTER)
            cell(row, 9, float(line.annual_contribution), fmt=money)
            cell(row, 10, line.remarks)
            row += 1
        cell(row, 1, 'TOTAL', size=11, bold=True, align=CENTER)
        cell(row, 9, float(section['subtotal']), size=10, bold=True, fmt=money)
        total_border_row(row)
        return row + 2

    for section in sections:
        r = section_table(r, section)

    # ── TOTAL COSTS (Annual) ──
    cell(r, 1, 'TOTAL COSTS (Annual)', size=12, bold=True)
    underline_row(r)
    r += 1
    for col, text in zip('FI', ['Section', 'Annual Total']):
        cell(r, ord(col) - 64, text, size=8, color=GRAY, align=CENTER)
    underline_row(r)
    r += 1
    for row_data in header.section_annual_breakdown():
        cell(r, 1, row_data['code'], align=CENTER)
        cell(r, 2, f"Total of {row_data['code']} — {row_data['label']}")
        cell(r, 9, float(row_data['annual_total']), fmt=money)
        r += 1
    cell(r, 1, 'GRAND TOTAL', size=11, bold=True, align=CENTER)
    cell(r, 9, float(header.total_annual_cost), size=10, bold=True, fmt=money)
    total_border_row(r)
    r += 2

    # ── Annual / Monthly / Daily cost ──
    cell(r, 3, 'ANNUAL COST'); cell(r, 9, float(header.total_annual_cost), fmt=money); r += 1
    cell(r, 3, 'MONTHLY COST'); cell(r, 9, float(header.total_monthly_cost), fmt=money); r += 1
    cell(r, 3, 'DAILY COST'); cell(r, 9, float(header.total_daily_cost), fmt=money); r += 2

    # ── Bid value (red labels, bold green figures — matches the source exactly) ──
    if can_see_bid_value:
        cell(r, 5, 'Proposed Contract Value', size=16, color=RED)
        cell(r, 9, float(header.proposed_contract_value), size=14, bold=True, color=GREEN, fmt=bid_money)
        r += 1
        for col, text in zip('MNOP', ['Daily', 'Monthly', 'Yearly', 'Qtrly']):
            cell(r, ord(col) - 64, text)
        r += 1
        contract_value = header.proposed_contract_value
        cell(r, 13, float(contract_value / 12 / 26), fmt=bid_money)
        cell(r, 14, float(contract_value / 12), fmt=bid_money)
        cell(r, 15, float(contract_value), fmt=bid_money)
        cell(r, 16, float(contract_value / 4), fmt=bid_money)
        r += 1
        cell(r, 5, 'Profit', size=16, color=RED)
        cell(r, 9, float(header.profit), size=14, bold=True, color=GREEN, fmt=bid_money)
        r += 2
        cell(r, 1, 'Applied VAT', size=16, color=RED)
        cell(r, 4, float(header.vat_rate) / 100, size=12, bold=True, color=GREEN, fmt='0.0%')
        cell(r, 5, 'Proposed Contract Value including VAT', size=16, color=RED)
        cell(r, 9, float(header.proposed_contract_value_incl_vat), size=14, bold=True, color=GREEN, fmt=bid_money)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f'First Year Maintenance - {project.project_name}.xlsx'.replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    # A browser or intermediate cache serving a stale copy of this download
    # would look identical to the bug being unfixed — rule that out outright.
    response['Cache-Control'] = 'no-store'
    wb.save(response)
    return response


def _attach_designations_cache(project, grade_lines):
    """One query for every designation on the project, grouped by grade
    code and stashed on each grade line — every headcount/monthly_cost/
    annual_cost access on these specific instances (the template's, and
    _gs_categories' own) then reuses it instead of re-querying per line."""
    by_code = {}
    for d in project.designation_manpower_lines.all():
        by_code.setdefault(d.grade_code, []).append(d)
    for line in grade_lines:
        line._designations_cache = by_code.get(line.grade_code, [])


def _gs_categories(project, grade_lines=None):
    if grade_lines is None:
        grade_lines = list(project.grade_structure_lines.all())
        _attach_designations_cache(project, grade_lines)
    categories = []
    for code, label in GradeStructureLine.CATEGORY_CHOICES:
        cat_lines = [line for line in grade_lines if line.category == code]
        categories.append({
            'code': code, 'label': label, 'lines': cat_lines,
            'headcount': sum((line.headcount for line in cat_lines), 0),
            'monthly_cost': sum((line.monthly_cost for line in cat_lines), Decimal('0')),
            'annual_cost': sum((line.annual_cost for line in cat_lines), Decimal('0')),
        })
    return categories


def _sync_grades_from_designations(project):
    """A Designation-wise Manpower row entered under a grade code that has
    no Individual Grade row yet would otherwise roll up nowhere — nothing
    to SUMIF into, same as the source workbook's own roster/grade split.
    Auto-create a minimal grade row for any such code so Individual Grades
    (and, through it, Summary by Category) picks it up immediately instead
    of silently dropping it; the PM can fill in criteria/reference salary
    afterwards if they want to."""
    existing_codes = set(project.grade_structure_lines.values_list('grade_code', flat=True))
    seen = set()
    for d in project.designation_manpower_lines.all():
        if not d.grade_code or d.grade_code in existing_codes or d.grade_code in seen:
            continue
        seen.add(d.grade_code)
        GradeStructureLine.objects.create(
            project=project, category=d.category, grade_code=d.grade_code,
            discipline=d.discipline, typical_designation=d.designation,
        )


def _eca_for(project):
    """The employment-cost-assumptions row, built in memory when none
    exists yet — same 'a GET must never write one' rule as
    _mpc_header_for."""
    return (getattr(project, 'employment_cost_assumptions', None)
            or EmploymentCostAssumptions(project=project))


@login_required
@delivery_required
def mpc_grade_structure_detail(request, pk):
    """Grade Structure: one page, like First Year Maintenance — Individual
    Grades and Designation-wise Manpower are both editable directly here,
    with no separate edit page to navigate to and back from."""
    project = _pm_visible_project_or_404(request, pk)
    can_edit = can_manage_manpower_costing(request.user)
    eca = _eca_for(project)

    if request.method == 'POST':
        if not can_edit:
            raise PermissionDenied('Only the Project Manager can edit the Grade Structure.')
        grade_formset = GradeStructureLineFormSet(request.POST, instance=project, prefix='grades')
        designation_formset = DesignationManpowerLineFormSet(
            request.POST, instance=project, prefix='designations')
        eca_form = EmploymentCostAssumptionsForm(request.POST, instance=eca)
        if grade_formset.is_valid() and designation_formset.is_valid() and eca_form.is_valid():
            with transaction.atomic():
                grade_formset.save()
                designation_formset.save()
                _sync_grades_from_designations(project)
                eca = eca_form.save(commit=False)
                eca.project = project
                eca.updated_by = request.user
                eca.save()
            messages.success(request, 'Grade Structure saved.')
            return redirect('pmo:mpc_grade_structure_detail', pk=project.pk)
    else:
        grade_formset = GradeStructureLineFormSet(instance=project, prefix='grades')
        designation_formset = DesignationManpowerLineFormSet(instance=project, prefix='designations')
        eca_form = EmploymentCostAssumptionsForm(instance=eca)

    grade_lines = [f.instance for f in grade_formset.forms]
    _attach_designations_cache(project, grade_lines)
    categories = _gs_categories(project, grade_lines=grade_lines)
    total_headcount = sum((c['headcount'] for c in categories), 0)
    for c in categories:
        c['pct_of_manpower'] = (c['headcount'] * 100 / total_headcount) if total_headcount else 0

    # Every designation row's Additional Cost / breakdown reads
    # _assumptions_cache instead of looking its own project's
    # EmploymentCostAssumptions up — one query for the whole table instead
    # of one per row, same reasoning as _attach_designations_cache above.
    for f in designation_formset.forms:
        f.instance._assumptions_cache = eca

    response = render(request, 'pmo/mpc_grade_structure_detail.html', {
        'project': project,
        'category_choices': GradeStructureLine.CATEGORY_CHOICES,
        'categories': categories,
        'total_headcount': total_headcount,
        'total_monthly_cost': sum((c['monthly_cost'] for c in categories), Decimal('0')),
        'total_annual_cost': sum((c['annual_cost'] for c in categories), Decimal('0')),
        'total_fully_loaded_monthly_cost': sum((line.fully_loaded_monthly_cost for line in grade_lines), Decimal('0')),
        'total_fully_loaded_annual_cost': sum((line.fully_loaded_annual_cost for line in grade_lines), Decimal('0')),
        'grade_formset': grade_formset,
        'designation_formset': designation_formset,
        'eca_form': eca_form,
        'discipline_suggestions': GradeStructureLine.DISCIPLINE_SUGGESTIONS,
        'can_edit': can_edit,
    })
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@delivery_required
def mpc_grade_structure_export_excel(request, pk):
    """Grade Structure as an .xlsx replicating the source workbook's own
    "Grade Structure" sheet: dark-blue table headers with white bold text,
    light-blue TOTAL rows, Calibri throughout (that sheet uses Calibri, not
    the Cambria of First Year Maintenance — verified per-sheet, not assumed)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    project = _pm_visible_project_or_404(request, pk)
    categories = _gs_categories(project)
    total_headcount = sum((c['headcount'] for c in categories), 0)
    total_monthly = sum((c['monthly_cost'] for c in categories), Decimal('0'))
    total_annual = sum((c['annual_cost'] for c in categories), Decimal('0'))
    total_fully_loaded_monthly = sum(
        (line.fully_loaded_monthly_cost for c in categories for line in c['lines']), Decimal('0'))
    total_fully_loaded_annual = total_fully_loaded_monthly * 12

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Grade Structure'
    for col, width in zip('ABCDEFGHIJKLM', [40, 35, 18, 35, 55, 14, 20, 20, 20, 18, 18, 20, 20]):
        ws.column_dimensions[col].width = width

    FONT = 'Calibri'
    NAVY = 'FF1F4E79'
    WHITE = 'FFFFFFFF'
    LIGHT_BLUE = 'FFDCE6F1'
    INPUT_BLUE = 'FF0000FF'
    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    number_fmt = '#,##0;\\(#,##0\\);\\-'
    CENTER = Alignment(horizontal='center')

    def cell(row, col, value=None, size=11, bold=False, color=None, fmt='General', align=None):
        c = ws.cell(row=row, column=col, value=value)
        c.font = Font(name=FONT, size=size, bold=bold, color=color)
        c.number_format = fmt
        c.border = border
        if align:
            c.alignment = align
        return c

    def header_row(row, headers):
        for col, text in enumerate(headers, start=1):
            c = cell(row, col, text, bold=True, color=WHITE, align=CENTER)
            c.fill = PatternFill('solid', fgColor=NAVY)

    def total_row(row, values):
        for col, value in enumerate(values, start=1):
            c = cell(row, col, value, bold=True,
                     fmt=number_fmt if isinstance(value, (int, float)) else 'General')
            c.fill = PatternFill('solid', fgColor=LIGHT_BLUE)

    ws.cell(row=1, column=1, value='MANPOWER GRADE STRUCTURE').font = Font(
        name=FONT, size=14, bold=True, color=NAVY)

    ws.cell(row=3, column=1, value='GRADE CATEGORIES').font = Font(
        name=FONT, size=12, bold=True, color=NAVY)
    header_row(4, ['Code', 'Category'])
    r = 5
    for code, label in GradeStructureLine.CATEGORY_CHOICES:
        cell(r, 1, code, align=CENTER)
        cell(r, 2, label)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value='INDIVIDUAL GRADES').font = Font(
        name=FONT, size=12, bold=True, color=NAVY)
    r += 1
    header_row(r, ['Category', 'Grade', 'Discipline / Specialization', 'Typical Designation',
                    'Criteria / Eligibility', 'Manpower (No.)', 'Indicative Monthly Salary (SAR)',
                    'Monthly Cost (SAR)', 'Annual Cost (SAR)', 'Fully Loaded Monthly Cost (SAR)',
                    'Fully Loaded Annual Cost (SAR)'])
    r += 1
    for category in categories:
        for line in category['lines']:
            cell(r, 1, category['label'])
            cell(r, 2, line.grade_code, align=CENTER)
            cell(r, 3, line.discipline, align=CENTER)
            cell(r, 4, line.typical_designation, align=CENTER)
            cell(r, 5, line.criteria)
            cell(r, 6, line.headcount, align=CENTER)
            cell(r, 7, float(line.indicative_monthly_salary), color=INPUT_BLUE, fmt=number_fmt)
            cell(r, 8, float(line.monthly_cost), fmt=number_fmt)
            cell(r, 9, float(line.annual_cost), fmt=number_fmt)
            cell(r, 10, float(line.fully_loaded_monthly_cost), fmt=number_fmt)
            cell(r, 11, float(line.fully_loaded_annual_cost), fmt=number_fmt)
            r += 1
    total_row(r, ['', '', '', '', 'TOTAL', total_headcount, '', float(total_monthly), float(total_annual),
                   float(total_fully_loaded_monthly), float(total_fully_loaded_annual)])
    r += 2

    ws.cell(row=r, column=1, value='SUMMARY BY CATEGORY').font = Font(
        name=FONT, size=12, bold=True, color=NAVY)
    r += 1
    header_row(r, ['Category', 'Code', 'Manpower (No.)', 'Monthly Cost (SAR)',
                    'Annual Cost (SAR)', '% of Manpower'])
    r += 1
    for category in categories:
        pct = (category['headcount'] / total_headcount) if total_headcount else 0
        cell(r, 1, category['label'])
        cell(r, 2, category['code'], align=CENTER)
        cell(r, 3, category['headcount'], align=CENTER)
        cell(r, 4, float(category['monthly_cost']), fmt=number_fmt)
        cell(r, 5, float(category['annual_cost']), fmt=number_fmt)
        cell(r, 6, pct, fmt='0.0%')
        r += 1
    total_pct = 1 if total_headcount else 0
    total_row(r, ['TOTAL', '', total_headcount, float(total_monthly), float(total_annual), total_pct])
    ws.cell(row=r, column=6).number_format = '0.0%'
    r += 2

    designations = list(project.designation_manpower_lines.all())
    eca = _eca_for(project)
    for d in designations:
        d._assumptions_cache = eca
    nationality_labels = dict(DesignationManpowerLine.NATIONALITY_CHOICES)
    ws.cell(row=r, column=1, value='DESIGNATION-WISE MANPOWER').font = Font(
        name=FONT, size=12, bold=True, color=NAVY)
    r += 1
    header_row(r, ['Designation', 'Category', 'Grade', 'Discipline / Specialization', 'Nationality',
                    'Manpower (No.)', 'Monthly Salary (SAR)', 'Additional Cost / Head (SAR)',
                    'Fully Loaded / Head (SAR)', 'Monthly Cost (SAR)', 'Annual Cost (SAR)',
                    'Fully Loaded Monthly Cost (SAR)', 'Fully Loaded Annual Cost (SAR)'])
    r += 1
    category_labels = dict(GradeStructureLine.CATEGORY_CHOICES)
    for d in designations:
        cell(r, 1, d.designation)
        cell(r, 2, category_labels.get(d.category, d.category), align=CENTER)
        cell(r, 3, d.grade_code, align=CENTER)
        cell(r, 4, d.discipline, align=CENTER)
        cell(r, 5, nationality_labels.get(d.nationality, d.nationality), align=CENTER)
        cell(r, 6, d.headcount, align=CENTER)
        cell(r, 7, float(d.monthly_salary), color=INPUT_BLUE, fmt=number_fmt)
        cell(r, 8, float(d.additional_cost_monthly), fmt=number_fmt)
        cell(r, 9, float(d.fully_loaded_monthly_cost), fmt=number_fmt)
        cell(r, 10, float(d.monthly_cost), fmt=number_fmt)
        cell(r, 11, float(d.annual_cost), fmt=number_fmt)
        cell(r, 12, float(d.fully_loaded_monthly_cost_total), fmt=number_fmt)
        cell(r, 13, float(d.fully_loaded_annual_cost_total), fmt=number_fmt)
        r += 1
    designation_headcount = sum((d.headcount for d in designations), 0)
    designation_monthly = sum((d.monthly_cost for d in designations), Decimal('0'))
    designation_annual = sum((d.annual_cost for d in designations), Decimal('0'))
    designation_fully_loaded_monthly = sum((d.fully_loaded_monthly_cost_total for d in designations), Decimal('0'))
    designation_fully_loaded_annual = sum((d.fully_loaded_annual_cost_total for d in designations), Decimal('0'))
    total_row(r, ['TOTAL', '', '', '', '', designation_headcount, '', '', '',
                   float(designation_monthly), float(designation_annual),
                   float(designation_fully_loaded_monthly), float(designation_fully_loaded_annual)])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    filename = f'Grade Structure - {project.project_name}.xlsx'.replace('/', '-')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-store'
    wb.save(response)
    return response


@login_required
@delivery_required
def mpc_engineer_rate_table(request, pk):
    project = _pm_visible_project_or_404(request, pk)
    return render(request, 'pmo/mpc_placeholder.html', {
        'project': project,
        'title': 'Engineer Rate Table',
        'description': 'Per-discipline rate build-up — coming in a later phase.',
        'icon': 'bi-calculator',
    })


@login_required
@delivery_required
def mpc_manpower_consolidated(request, pk):
    project = _pm_visible_project_or_404(request, pk)
    return render(request, 'pmo/mpc_placeholder.html', {
        'project': project,
        'title': 'Manpower Consolidated',
        'description': 'A single live-linked view across Grade Structure, Rate Tables and '
                        'First Year Maintenance — coming in a later phase.',
        'icon': 'bi-diagram-3',
    })
