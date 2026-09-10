from django.urls import path
from . import views

app_name = 'pm_dashboard'

urlpatterns = [
    path('', views.project_list, name='index'),
    path('<int:pk>/', views.project_overview, name='project_overview'),
    path('<int:pk>/po/', views.project_po, name='project_po'),
    path('<int:pk>/boq/', views.project_boq, name='project_boq'),
    path('<int:pk>/responsibility/', views.responsibility_matrix_detail, name='responsibility_matrix_detail'),
    path('<int:pk>/responsibility/edit/', views.responsibility_matrix_edit, name='responsibility_matrix_edit'),
    path('<int:pk>/communication/', views.communication_matrix_detail, name='communication_matrix_detail'),
    path('<int:pk>/communication/edit/', views.communication_matrix_edit, name='communication_matrix_edit'),
    path('<int:pk>/communication/export-pdf/', views.communication_matrix_export_pdf, name='communication_matrix_export_pdf'),
]
