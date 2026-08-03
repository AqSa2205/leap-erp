# engineer_calendar/views.py
import json
import calendar
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.http import require_POST

from accounts.permissions import require_capability
from .models import CalendarCell
from .services import generate_draft
from hr.models import Employee
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from django.http import JsonResponse, HttpResponse

# engineer_calendar/views.py — add this near the top
SOURCE_CSS = {
    'leave': 'src-leave',
    'holiday': 'src-holiday',
    'weekend': 'src-weekend',
    'wfh': 'src-wfh',
    'timesheet': 'src-timesheet',
    'manual': 'src-manual',
    'blank': 'src-blank',
}




@login_required
@require_capability('engineer_calendar.access')
def export_excel(request):
    today = date.today()
    year, month = today.year, today.month
    days_in_month = calendar.monthrange(year, month)[1]

    employees = Employee.objects.filter(is_active=True)
    cells = CalendarCell.objects.filter(date__year=year, date__month=month).select_related('employee')
    cell_map = {}
    for cell in cells:
        cell_map.setdefault(cell.employee_id, {})[cell.date.day] = cell

    wb = Workbook()
    ws = wb.active
    ws.title = calendar.month_abbr[month].upper()

    thin = Side(style='thin')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center')
    yellow = PatternFill('solid', fgColor='FFF3CD')
    header_font = Font(name='Cambria', bold=True)
    normal_font = Font(name='Cambria')

    ws['A1'] = 'Leap Networks Arabia'
    ws['A2'] = 'Al-Khobar, Saudi Arabia'
    ws['A4'] = 'Resource Calendar (Detailed)'
    ws['A5'] = 'Month'
    ws['B5'] = date(year, month, 1)

    headers = ['S. No.', 'Employee Name', 'Department'] + list(range(1, days_in_month + 1)) + ['Remarks']
    for col, val in enumerate(headers, start=1):
        c = ws.cell(row=7, column=col, value=val)
        c.font = header_font
        c.alignment = center
        c.border = border

    for day in range(1, days_in_month + 1):
        weekday_letter = calendar.day_abbr[date(year, month, day).weekday()][0]
        ws.cell(row=9, column=3 + day, value=weekday_letter).alignment = center

    row = 10
    for i, emp in enumerate(employees, start=1):
        ws.cell(row=row, column=1, value=i).border = border
        ws.cell(row=row, column=2, value=emp.full_name).border = border
        ws.cell(row=row, column=3, value=emp.designation).border = border

        emp_cells = cell_map.get(emp.id, {})
        for day in range(1, days_in_month + 1):
            cell = emp_cells.get(day)
            xcell = ws.cell(row=row, column=3 + day, value=cell.display_text if cell else '')
            xcell.font = normal_font
            xcell.alignment = center
            xcell.border = border
            if cell and cell.source == 'weekend':
                xcell.fill = yellow

        ws.cell(row=row, column=4 + days_in_month, value='').border = border  # Remarks — blank for now
        row += 1
        row += 1  # employment-type/DOJ row — blank for now
        row += 1  # spacer row

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="Engineer_Calendar_{calendar.month_abbr[month]}_{year}.xlsx"'
    wb.save(response)
    return response

@login_required
@require_capability('engineer_calendar.access')
@require_POST
def save_cell(request):
    try:
        payload = json.loads(request.body)
        employee_id = payload['employee_id']
        cell_date = date.fromisoformat(payload['date'])
        text = payload.get('text', '').strip()
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    employee = get_object_or_404(Employee, pk=employee_id)

    cell, _created = CalendarCell.objects.get_or_create(
        employee=employee,
        date=cell_date,
        defaults={'source': 'manual'},
    )
    cell.display_text = text
    cell.source = 'manual'
    cell.needs_review = False
    cell.updated_by = request.user
    cell.save()

    return JsonResponse({'text': cell.display_text, 'css_class': 'src-manual'})


@login_required
@require_capability('engineer_calendar.access')
def calendar_grid(request):
    today = date.today()
    year, month = today.year, today.month
    days_in_month = calendar.monthrange(year, month)[1]

    employees = Employee.objects.filter(is_active=True)

    cells = CalendarCell.objects.filter(
        date__year=year, date__month=month
    ).select_related('employee')

    # Build employee_id -> {day_number: cell}
    cell_map = {}
    for cell in cells:
        cell_map.setdefault(cell.employee_id, {})[cell.date.day] = cell

    rows = []
    for emp in employees:
        emp_cells = cell_map.get(emp.id, {})
        day_cells = []
        for day in range(1, days_in_month + 1):
            cell = emp_cells.get(day)
            day_cells.append({
                'text': cell.display_text if cell else '',
                'css_class': SOURCE_CSS.get(cell.source, 'src-blank') if cell else 'src-blank',
                'needs_review': cell.needs_review if cell else False,
                'date': date(year, month, day).isoformat(),
            })
        rows.append({'employee': emp, 'day_cells': day_cells})

    weekday_letters = [
        calendar.day_abbr[date(year, month, day).weekday()][0]
        for day in range(1, days_in_month + 1)
    ]

    context = {
        'year': year,
        'month': month,
        'month_name': calendar.month_name[month],
        'day_numbers': range(1, days_in_month + 1),
        'weekday_letters': weekday_letters,
        'rows': rows,
    }
    return render(request, 'engineer_calendar/calendar_grid.html', context)


@login_required
@require_capability('engineer_calendar.access')
def generate_draft_view(request):
    if request.method == 'POST':
        today = date.today()
        generate_draft(Employee.objects.filter(is_active=True), year=today.year, month=today.month)
        messages.success(request, 'Calendar draft regenerated.')
    return redirect('engineer_calendar:grid')