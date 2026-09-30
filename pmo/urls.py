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
    path('manpower/dashboard/', views.manpower_dashboard, name='manpower_dashboard'),
    path('manpower/dashboard/project/<int:project_pk>/', views.manpower_project_breakdown, name='manpower_project_breakdown'),
    path('manpower/add/', views.manpower_create, name='manpower_create'),
    path('manpower/<int:employee_pk>/edit/', views.manpower_edit, name='manpower_edit'),
    path('manpower/<int:employee_pk>/clear/', views.manpower_clear, name='manpower_clear'),
    path('manpower/<int:employee_pk>/assign/', views.manpower_assign, name='manpower_assign'),

    # Issue Log: the delivery team's risk/issue register, one month at a time.
    path('issues/', views.issue_log_list, name='issue_log_list'),
    path('issues/add/', views.issue_log_create, name='issue_log_create'),
    path('issues/<int:pk>/edit/', views.issue_log_edit, name='issue_log_edit'),
    path('issues/export/', views.issue_log_export_excel, name='issue_log_export_excel'),
    path('issues/export/all/', views.issue_log_export_all_zip, name='issue_log_export_all_zip'),
]
