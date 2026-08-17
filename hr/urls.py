from django.urls import path
from . import views

app_name = 'hr'

urlpatterns = [
    path('', views.hr_dashboard, name='hr_dashboard'),
    path('my-profile/', views.my_profile, name='my_profile'),
    path('my-profile/attendance/export/pdf/', views.my_attendance_export_pdf, name='my_attendance_export_pdf'),
    path('my-profile/late-query/raise/', views.raise_late_query, name='raise_late_query'),
    path('employees/', views.EmployeeListView.as_view(), name='employee_list'),
    path('create/', views.EmployeeCreateView.as_view(), name='employee_create'),
    path('import/', views.employee_import, name='employee_import'),
    path('export/', views.employee_export, name='employee_export'),
    path('<int:pk>/', views.EmployeeDetailView.as_view(), name='employee_detail'),
    path('<int:pk>/edit/', views.EmployeeUpdateView.as_view(), name='employee_update'),
    path('<int:pk>/delete/', views.EmployeeDeleteView.as_view(), name='employee_delete'),

    # Employee Documents
    path('<int:pk>/upload-document/', views.employee_document_upload, name='employee_doc_upload'),
    path('document/<int:pk>/delete/', views.employee_document_delete, name='employee_doc_delete'),

    # Assets (Laptops & Equipment)
    path('assets/', views.AssetListView.as_view(), name='asset_list'),
    path('assets/create/', views.AssetCreateView.as_view(), name='asset_create'),
    path('assets/import/', views.asset_import, name='asset_import'),
    path('assets/export/', views.asset_export, name='asset_export'),
    path('assets/<int:pk>/', views.AssetDetailView.as_view(), name='asset_detail'),
    path('assets/<int:pk>/edit/', views.AssetUpdateView.as_view(), name='asset_update'),
    path('assets/<int:pk>/delete/', views.AssetDeleteView.as_view(), name='asset_delete'),
    path('assets/<int:pk>/issue/', views.AssetIssueView.as_view(), name='asset_issue'),
    path('assets/<int:pk>/return/', views.AssetReturnView.as_view(), name='asset_return'),
    path('assets/<int:pk>/decommission/', views.asset_decommission, name='asset_decommission'),
    path('assets/<int:pk>/restore/', views.asset_restore, name='asset_restore'),

    # Vehicles
    path('vehicles/', views.VehicleListView.as_view(), name='vehicle_list'),
    path('vehicles/create/', views.VehicleCreateView.as_view(), name='vehicle_create'),
    path('vehicles/import/', views.vehicle_import, name='vehicle_import'),
    path('vehicles/export/', views.vehicle_export, name='vehicle_export'),
    path('vehicles/<int:pk>/', views.VehicleDetailView.as_view(), name='vehicle_detail'),
    path('vehicles/<int:pk>/edit/', views.VehicleUpdateView.as_view(), name='vehicle_update'),
    path('vehicles/<int:pk>/delete/', views.VehicleDeleteView.as_view(), name='vehicle_delete'),
    path('vehicles/<int:pk>/upload-document/', views.vehicle_document_upload, name='vehicle_doc_upload'),
    path('vehicle-document/<int:pk>/edit/', views.vehicle_document_edit, name='vehicle_doc_edit'),
    path('vehicle-document/<int:pk>/delete/', views.vehicle_document_delete, name='vehicle_doc_delete'),

    # Leave Types
    path('leave-types/', views.LeaveTypeListView.as_view(), name='leavetype_list'),
    path('leave-types/create/', views.LeaveTypeCreateView.as_view(), name='leavetype_create'),
    path('leave-types/<int:pk>/edit/', views.LeaveTypeUpdateView.as_view(), name='leavetype_update'),
    path('leave-types/<int:pk>/delete/', views.LeaveTypeDeleteView.as_view(), name='leavetype_delete'),

    # Holidays
    path('holidays/', views.HolidayListView.as_view(), name='holiday_list'),
    path('holidays/create/', views.HolidayCreateView.as_view(), name='holiday_create'),
    path('holidays/<int:pk>/edit/', views.HolidayUpdateView.as_view(), name='holiday_update'),
    path('holidays/<int:pk>/delete/', views.HolidayDeleteView.as_view(), name='holiday_delete'),

    # Working Days
    path('working-days/', views.WorkingDayListView.as_view(), name='workingday_list'),
    path('working-days/create/', views.WorkingDayCreateView.as_view(), name='workingday_create'),
    path('working-days/<int:pk>/edit/', views.WorkingDayUpdateView.as_view(), name='workingday_update'),
    path('working-days/<int:pk>/delete/', views.WorkingDayDeleteView.as_view(), name='workingday_delete'),

    # Leave Records, Summary & Entitlements
    path('leave/record/create/', views.LeaveRequestCreateView.as_view(), name='leave_record_create'),
    path('leave/record/<int:pk>/delete/', views.LeaveRecordDeleteView.as_view(), name='leave_record_delete'),
    path('leave-requests/', views.LeaveRequestListView.as_view(), name='leave_request_list'),
    path('leave-requests/create/', views.LeaveRequestCreateView.as_view(), name='leave_request_create'),
    path('leave-requests/<int:pk>/document/', views.leave_request_document_download, name='leave_request_document'),
    path('leave-requests/<int:pk>/', views.LeaveRequestDetailView.as_view(), name='leave_request_detail'),
    path('leave/entitlements/', views.entitlement_year, name='entitlement_year'),
    path('leave/access/', views.LeaveAccessView.as_view(), name='leave_access'),
    path('<int:pk>/leave/', views.EmployeeLeaveSummaryView.as_view(), name='leave_summary'),
    path('<int:pk>/leave/grant-exception/', views.EmployeeGrantExceptionDaysView.as_view(), name='grant_exception_days'),
    path('<int:pk>/attendance/', views.AttendanceHistoryView.as_view(), name='attendance_history'),

    # WFH Records
    path('wfh/', views.WFHRecordListView.as_view(), name='wfh_list'),
    path('wfh/create/', views.WFHRecordCreateView.as_view(), name='wfh_create'),
    path('wfh/<int:pk>/delete/', views.WFHRecordDeleteView.as_view(), name='wfh_delete'),

    # Attendance Exceptions
    path('attendance-exceptions/', views.TeamExceptionsView.as_view(), name='team_exceptions'),

    # Org-Chart Management (Super Admin only)
    path('org-chart/', views.OrgChartView.as_view(), name='org_chart'),

    # Attendance
    path('attendance/settings/', views.attendance_settings, name='attendance_settings'),
    path('attendance/regenerate/', views.attendance_regenerate, name='attendance_regenerate'),
    path('attendance/matrix/', views.attendance_matrix, name='attendance_matrix'),
    path('attendance/matrix/export/excel/', views.attendance_matrix_export_excel, name='attendance_matrix_export_excel'),
    path('attendance/matrix/export/pdf/', views.attendance_matrix_export_pdf, name='attendance_matrix_export_pdf'),
    path('attendance/mark-leave/', views.attendance_mark_leave, name='attendance_mark_leave'),
    path('attendance/unmark-leave/', views.attendance_unmark_leave, name='attendance_unmark_leave'),
    path('attendance/', views.attendance_grid, name='attendance_grid'),
    # Asset Handover
    path('assets/handovers/', views.asset_handover_list, name='asset_handover_list'),
    path('assets/handovers/create/', views.asset_handover_create, name='asset_handover_create'),
    path('assets/handovers/<int:pk>/', views.asset_handover_detail, name='asset_handover_detail'),
    path('assets/handovers/<int:pk>/authorize/', views.asset_handover_authorize, name='asset_handover_authorize'),
    path('assets/handovers/<int:pk>/receive/', views.asset_handover_receive, name='asset_handover_receive'),
    path('assets/handovers/<int:pk>/return/', views.asset_handover_initiate_return, name='asset_handover_initiate_return'),
    path('assets/handovers/<int:pk>/acknowledge-return/', views.asset_handover_acknowledge_return, name='asset_handover_acknowledge_return'),
    path('assets/handovers/<int:pk>/export-pdf/', views.asset_handover_export_pdf, name='asset_handover_export_pdf'),
    path('assets/<int:item_id>/active-handover/', views.asset_active_handover_redirect, name='asset_active_handover_redirect'),
]
