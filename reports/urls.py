from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.index, name='index'),
    path('export/', views.export_excel, name='export'),
    path('import/', views.import_excel, name='import'),
    path('summary/', views.summary_report, name='summary'),
    path('annual-report/', views.annual_report, name='annual_report'),
]
