from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.index, name='index'),
    path('export/', views.export_excel, name='export'),
    path('import/', views.import_excel, name='import'),
    path('summary/', views.summary_report, name='summary'),
    path('annual-report/', views.annual_report, name='annual_report'),

    # Sales Call Reports
    path('sales-calls/', views.SalesCallReportListView.as_view(), name='sales_call_list'),
    path('sales-calls/add/', views.SalesCallReportCreateView.as_view(), name='sales_call_create'),
    path('sales-calls/<int:pk>/', views.SalesCallReportDetailView.as_view(), name='sales_call_detail'),
    path('sales-calls/<int:pk>/edit/', views.SalesCallReportUpdateView.as_view(), name='sales_call_update'),
    path('sales-calls/<int:pk>/delete/', views.SalesCallReportDeleteView.as_view(), name='sales_call_delete'),
    path('sales-calls/export/', views.export_sales_call_reports, name='sales_call_export'),
]
