import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import require_capability
from dashboard.views import projects_visible_to

from .models import (
    CommunicationMatrix,
    ResponsibilityMatrix,
    default_communication_columns,
    default_responsibility_columns,
    sanitize_grid,
)


def _visible_project_or_404(request, pk):
    """Same 'which projects can this person see' ladder used by the main
    dashboard — a PM should not be able to reach a project via this
    dashboard that they could not already reach via the pipeline."""
    return get_object_or_404(projects_visible_to(request.user), pk=pk)


@login_required
@require_capability('pm_dashboard.access')
def project_list(request):
    """Portfolio landing page: graphical overview of every project this user
    can see, then the searchable table to pick one and drill in."""
    projects_qs = projects_visible_to(request.user).select_related('region', 'status')

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

    # ---- Portfolio-wide KPIs & charts (over everything visible, not the
    # search-filtered subset — the charts describe the whole portfolio the
    # search box lets you narrow down from). ----
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

    return render(request, 'pm_dashboard/project_list.html', {
        'projects': filtered,
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


@login_required
@require_capability('pm_dashboard.access')
def project_overview(request, pk):
    """Graphical snapshot of one project — status tiles + a milestone chart,
    then cards linking into the dedicated PO / BOQ / matrix pages. This page
    itself carries no PO/BOQ/matrix data, only counts, so there is nothing
    here that could get one project's records mixed up with another's."""
    project = _visible_project_or_404(request, pk)
    po_document_count = project.documents.filter(document_type='po_document').count()
    costing_sheet_count = project.costing_sheets.count()
    responsibility_matrix = getattr(project, 'responsibility_matrix', None)
    communication_matrix = getattr(project, 'communication_matrix', None)
    return render(request, 'pm_dashboard/project_overview.html', {
        'project': project,
        'po_document_count': po_document_count,
        'costing_sheet_count': costing_sheet_count,
        'responsibility_row_count': len(responsibility_matrix.rows) if responsibility_matrix else 0,
        'communication_row_count': len(communication_matrix.rows) if communication_matrix else 0,
        'milestone_rows_json': json.dumps([
            {'label': row['label'], 'variance_display': row['variance_display']}
            for row in project.milestone_display_rows
        ]),
    })


@login_required
@require_capability('pm_dashboard.access')
def project_po(request, pk):
    project = _visible_project_or_404(request, pk)
    po_documents = project.documents.filter(document_type='po_document').order_by('-uploaded_at')
    return render(request, 'pm_dashboard/project_po.html', {
        'project': project,
        'po_documents': po_documents,
    })


@login_required
@require_capability('pm_dashboard.access')
def project_boq(request, pk):
    project = _visible_project_or_404(request, pk)
    costing_sheets = project.costing_sheets.all().order_by('-updated_at')
    return render(request, 'pm_dashboard/project_boq.html', {
        'project': project,
        'costing_sheets': costing_sheets,
    })


@login_required
@require_capability('pm_dashboard.access')
def responsibility_matrix_detail(request, pk):
    project = _visible_project_or_404(request, pk)
    matrix = getattr(project, 'responsibility_matrix', None)
    return render(request, 'pm_dashboard/responsibility_matrix_detail.html', {
        'project': project,
        'columns': matrix.columns if matrix else default_responsibility_columns(),
        'rows': matrix.rows if matrix else [],
    })


@login_required
@require_capability('pm_dashboard.access')
def communication_matrix_detail(request, pk):
    project = _visible_project_or_404(request, pk)
    matrix = getattr(project, 'communication_matrix', None)
    return render(request, 'pm_dashboard/communication_matrix_detail.html', {
        'project': project,
        'columns': matrix.columns if matrix else default_communication_columns(),
        'rows': matrix.rows if matrix else [],
    })


@login_required
@require_capability('pm_dashboard.access')
def responsibility_matrix_edit(request, pk):
    project = _visible_project_or_404(request, pk)
    matrix, _ = ResponsibilityMatrix.objects.get_or_create(project=project)
    if request.method == 'POST':
        try:
            columns_raw = json.loads(request.POST.get('columns_json') or '[]')
            rows_raw = json.loads(request.POST.get('rows_json') or '[]')
        except (ValueError, TypeError):
            columns_raw, rows_raw = [], []
        matrix.columns, matrix.rows = sanitize_grid(
            columns_raw, rows_raw, default_responsibility_columns())
        matrix.updated_by = request.user
        matrix.save()
        messages.success(request, 'Responsibility matrix updated.')
        return redirect('pm_dashboard:responsibility_matrix_detail', pk=project.pk)
    return render(request, 'pm_dashboard/matrix_edit.html', {
        'project': project,
        'matrix_title': 'Responsibility Matrix',
        'cancel_url_name': 'pm_dashboard:responsibility_matrix_detail',
        'columns_json': json.dumps(matrix.columns or default_responsibility_columns()),
        'rows_json': json.dumps(matrix.rows or []),
    })


@login_required
@require_capability('pm_dashboard.access')
def communication_matrix_edit(request, pk):
    project = _visible_project_or_404(request, pk)
    matrix, _ = CommunicationMatrix.objects.get_or_create(project=project)
    if request.method == 'POST':
        try:
            columns_raw = json.loads(request.POST.get('columns_json') or '[]')
            rows_raw = json.loads(request.POST.get('rows_json') or '[]')
        except (ValueError, TypeError):
            columns_raw, rows_raw = [], []
        matrix.columns, matrix.rows = sanitize_grid(
            columns_raw, rows_raw, default_communication_columns())
        matrix.updated_by = request.user
        matrix.save()
        messages.success(request, 'Communication matrix updated.')
        return redirect('pm_dashboard:communication_matrix_detail', pk=project.pk)
    return render(request, 'pm_dashboard/matrix_edit.html', {
        'project': project,
        'matrix_title': 'Communication Matrix',
        'cancel_url_name': 'pm_dashboard:communication_matrix_detail',
        'columns_json': json.dumps(matrix.columns or default_communication_columns()),
        'rows_json': json.dumps(matrix.rows or []),
    })


@login_required
@require_capability('pm_dashboard.access')
def communication_matrix_export_pdf(request, pk):
    """Client-facing PDF report of the communication matrix — plain table,
    no pricing/internal data, safe to share externally."""
    from io import BytesIO

    from django.utils import timezone
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    project = _visible_project_or_404(request, pk)
    matrix, _ = CommunicationMatrix.objects.get_or_create(project=project)
    columns = matrix.columns or default_communication_columns()
    rows = matrix.rows or []

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
