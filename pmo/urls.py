from django.urls import path

from . import views

app_name = 'pmo'

urlpatterns = [
    path('', views.board, name='board'),
    path('project/<int:pk>/', views.project_detail, name='project_detail'),
    path('milestone/<int:pk>/progress/', views.update_progress, name='update_progress'),
    path('import/', views.milestone_import, name='milestone_import'),
    path('import/apply/', views.milestone_import_apply, name='milestone_import_apply'),

    # PM Dashboard: client PO, unpriced BOQ, responsibility & communication —
    # a second view onto the same projects `board` covers.
    path('dashboard/', views.pm_dashboard_index, name='pm_dashboard_index'),
    path('dashboard/export/', views.pm_dashboard_export_excel, name='pm_dashboard_export_excel'),
    path('dashboard/<int:pk>/', views.pm_project_overview, name='pm_project_overview'),
    path('dashboard/<int:pk>/po/', views.pm_project_po, name='pm_project_po'),
    path('dashboard/<int:pk>/boq/', views.pm_project_boq, name='pm_project_boq'),
    path('dashboard/<int:pk>/responsibility/', views.pm_responsibility_matrix, name='pm_responsibility_matrix'),
    path('dashboard/<int:pk>/responsibility/edit/', views.pm_responsibility_matrix_edit, name='pm_responsibility_matrix_edit'),
    path('dashboard/<int:pk>/communication/', views.pm_communication_matrix, name='pm_communication_matrix'),
    path('dashboard/<int:pk>/communication/edit/', views.pm_communication_matrix_edit, name='pm_communication_matrix_edit'),
    path('dashboard/<int:pk>/communication/export-pdf/', views.pm_communication_matrix_export_pdf, name='pm_communication_matrix_export_pdf'),

    # Manpower Status: every HR employee, with the extra fields Project
    # Management tracks about them.
    path('manpower/', views.manpower_list, name='manpower_list'),
    path('manpower/add/', views.manpower_create, name='manpower_create'),
    path('manpower/<int:employee_pk>/edit/', views.manpower_edit, name='manpower_edit'),
    path('manpower/<int:employee_pk>/clear/', views.manpower_clear, name='manpower_clear'),

    # Issue Log: the delivery team's risk/issue register, one month at a time.
    path('issues/', views.issue_log_list, name='issue_log_list'),
    path('issues/add/', views.issue_log_create, name='issue_log_create'),
    path('issues/<int:pk>/edit/', views.issue_log_edit, name='issue_log_edit'),
    path('issues/export/', views.issue_log_export_excel, name='issue_log_export_excel'),
    path('issues/export/all/', views.issue_log_export_all_zip, name='issue_log_export_all_zip'),

    # Project Manpower Costing: bid-stage staffing-cost estimation per project.
    # Nested under /delivery/, so this is /delivery/manpower-costing/... —
    # distinct from the top-level /manpower-costing/ mount of the unrelated
    # manpowercost (HR payroll) app in erp_leap/urls.py. URL *names* also get
    # their own mpc_ prefix so base.html's substring-based active-link checks
    # can't confuse the two.
    path('manpower-costing/', views.mpc_index, name='mpc_index'),
    path('manpower-costing/<int:pk>/', views.mpc_project_overview, name='mpc_project_overview'),
    path('manpower-costing/<int:pk>/first-year-maintenance/', views.mpc_first_year_detail, name='mpc_first_year_detail'),
    path('manpower-costing/<int:pk>/first-year-maintenance/export/', views.mpc_first_year_export_excel, name='mpc_first_year_export_excel'),
    path('manpower-costing/<int:pk>/grade-structure/', views.mpc_grade_structure_detail, name='mpc_grade_structure_detail'),
    path('manpower-costing/<int:pk>/grade-structure/export/', views.mpc_grade_structure_export_excel, name='mpc_grade_structure_export_excel'),
    path('manpower-costing/<int:pk>/engineer-rate-table/', views.mpc_engineer_rate_table, name='mpc_engineer_rate_table'),
    path('manpower-costing/<int:pk>/manpower-consolidated/', views.mpc_manpower_consolidated, name='mpc_manpower_consolidated'),
]
