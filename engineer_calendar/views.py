# engineer_calendar/views.py
import json
import calendar
from datetime import date, timedelta

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
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    header_font = Font(name='Cambria', size=10, bold=True)
    normal_font = Font(name='Cambria', size=10)

    # Real colors pulled from HR's actual template — see explanation above
    FILL_WEEKEND = PatternFill('solid', fgColor='C00000')
    FILL_HOLIDAY = PatternFill('solid', fgColor='C00000')   # same as weekend — no real sample found
    FILL_LEAVE   = PatternFill('solid', fgColor='FBE5D6')
    FILL_WORK    = PatternFill('solid', fgColor='E2EFDA')   # timesheet / wfh / manual entries
    FILL_NAME_ROW = PatternFill('solid', fgColor='DEEBF7')
    FILL_SPACER   = PatternFill('solid', fgColor='F2F2F2')

    SOURCE_FILL = {
        'weekend': FILL_WEEKEND,
        'holiday': FILL_HOLIDAY,
        'leave': FILL_LEAVE,
        'wfh': FILL_WORK,
        'timesheet': FILL_WORK,
        'manual': FILL_WORK,
    }

    ws['A1'] = 'Leap Networks Arabia'
    ws['A1'].font = Font(name='Cambria', size=11, bold=True)
    ws['A2'] = 'Al-Khobar, Saudi Arabia'
    ws['A2'].font = header_font
    ws['A4'] = 'Resource Calendar (Detailed)'
    ws['A4'].font = header_font
    ws['A5'] = 'Month'
    ws['A5'].font = header_font
    ws['B5'] = date(year, month, 1)
    ws['B5'].font = Font(name='Cambria', size=10, bold=True, color='FF0000')

    # Column widths matching the real template
    ws.column_dimensions['A'].width = 7.1
    ws.column_dimensions['B'].width = 19.4
    ws.column_dimensions['C'].width = 16.7
    for day in range(1, days_in_month + 1):
        col_letter = ws.cell(row=1, column=3 + day).column_letter
        ws.column_dimensions[col_letter].width = 9.1
    remarks_col = ws.cell(row=1, column=4 + days_in_month).column_letter
    ws.column_dimensions[remarks_col].width = 12.7

    headers = ['S. No.', 'Employee Name', 'Department'] + list(range(1, days_in_month + 1)) + ['Remarks']
    for col, val in enumerate(headers, start=1):
        c = ws.cell(row=7, column=col, value=val)
        c.font = header_font
        c.alignment = center
        c.border = border

    for day in range(1, days_in_month + 1):
        weekday_letter = calendar.day_abbr[date(year, month, day).weekday()][0]
        c = ws.cell(row=9, column=3 + day, value=weekday_letter)
        c.font = header_font
        c.alignment = center

    row = 10
    for i, emp in enumerate(employees, start=1):
        ws.row_dimensions[row].height = 40.2

        for col, val in [(1, i), (2, emp.full_name), (3, emp.designation)]:
            c = ws.cell(row=row, column=col, value=val)
            c.font = normal_font
            c.alignment = center
            c.border = border
            c.fill = FILL_NAME_ROW

        emp_cells = cell_map.get(emp.id, {})
        for day in range(1, days_in_month + 1):
            cell = emp_cells.get(day)
            xcell = ws.cell(row=row, column=3 + day, value=cell.display_text if cell else '')
            xcell.font = normal_font
            xcell.alignment = center
            xcell.border = border
            if cell and cell.source in SOURCE_FILL:
                xcell.fill = SOURCE_FILL[cell.source]

        remarks_cell = ws.cell(row=row, column=4 + days_in_month, value='')  # Remarks — blank for now
        remarks_cell.border = border
        remarks_cell.alignment = center

        row += 1  # employment-type/DOJ row — blank for now
        ws.row_dimensions[row].height = 15
        row += 1
        ws.row_dimensions[row].height = 8
        ws.cell(row=row, column=1).fill = FILL_SPACER  # spacer row tint
        row += 1

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
@require_POST
def fill_range(request):
    try:
        payload = json.loads(request.body)
        employee_id = payload['employee_id']
        start_date = date.fromisoformat(payload['start_date'])
        end_date = date.fromisoformat(payload['end_date'])
        text = payload.get('text', '').strip()
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    if end_date < start_date:
        return JsonResponse({'error': 'End date must be on or after the start date.'}, status=400)

    employee = get_object_or_404(Employee, pk=employee_id)

    updated_days = []
    current = start_date
    while current <= end_date:
        cell, _created = CalendarCell.objects.get_or_create(
            employee=employee, date=current, defaults={'source': 'manual'},
        )
        cell.display_text = text
        cell.source = 'manual'
        cell.needs_review = False
        cell.updated_by = request.user
        cell.save()
        updated_days.append(current.day)
        current += timedelta(days=1)

    return JsonResponse({'text': text, 'css_class': 'src-manual', 'updated_days': updated_days})


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