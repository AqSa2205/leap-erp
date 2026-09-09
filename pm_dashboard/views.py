import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
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
    projects = projects_visible_to(request.user).select_related('region', 'status')
    q = (request.GET.get('q') or '').strip()
    if q:
        projects = projects.filter(
            Q(project_name__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(customer__icontains=q)
            | Q(po_number__icontains=q)
        )
    projects = projects.order_by('-id')
    return render(request, 'pm_dashboard/project_list.html', {
        'projects': projects,
        'q': q,
    })


@login_required
@require_capability('pm_dashboard.access')
def project_detail(request, pk):
    project = _visible_project_or_404(request, pk)
    po_documents = project.documents.filter(document_type='po_document').order_by('-uploaded_at')
    costing_sheets = project.costing_sheets.all().order_by('-updated_at')
    responsibility_matrix = getattr(project, 'responsibility_matrix', None)
    communication_matrix = getattr(project, 'communication_matrix', None)
    return render(request, 'pm_dashboard/project_detail.html', {
        'project': project,
        'po_documents': po_documents,
        'costing_sheets': costing_sheets,
        'responsibility_columns': (responsibility_matrix.columns if responsibility_matrix
                                    else default_responsibility_columns()),
        'responsibility_rows': (responsibility_matrix.rows if responsibility_matrix else []),
        'communication_columns': (communication_matrix.columns if communication_matrix
                                   else default_communication_columns()),
        'communication_rows': (communication_matrix.rows if communication_matrix else []),
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
        return redirect('pm_dashboard:project_detail', pk=project.pk)
    return render(request, 'pm_dashboard/matrix_edit.html', {
        'project': project,
        'matrix_title': 'Responsibility Matrix',
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
        return redirect('pm_dashboard:project_detail', pk=project.pk)
    return render(request, 'pm_dashboard/matrix_edit.html', {
        'project': project,
        'matrix_title': 'Communication Matrix',
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
