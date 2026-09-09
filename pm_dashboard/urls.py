from django.urls import path
from . import views

app_name = 'pm_dashboard'

urlpatterns = [
    path('', views.project_list, name='index'),
    path('<int:pk>/', views.project_detail, name='project_detail'),
    path('<int:pk>/responsibility/edit/', views.responsibility_matrix_edit, name='responsibility_matrix_edit'),
    path('<int:pk>/communication/edit/', views.communication_matrix_edit, name='communication_matrix_edit'),
    path('<int:pk>/communication/export-pdf/', views.communication_matrix_export_pdf, name='communication_matrix_export_pdf'),
]
