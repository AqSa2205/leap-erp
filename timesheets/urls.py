from django.urls import path

from . import views

app_name = 'timesheets'

urlpatterns = [
    path('', views.my_timesheet, name='my_timesheet'),
    path('entry/<int:pk>/edit/', views.timesheet_entry_edit, name='entry_edit'),
    path('entry/<int:pk>/delete/', views.timesheet_entry_delete, name='entry_delete'),
    path('export/', views.timesheet_export, name='export'),
]