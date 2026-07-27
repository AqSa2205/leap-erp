from django.urls import path

from . import views

app_name = 'timesheets'

urlpatterns = [
    path('', views.my_timesheet, name='my_timesheet'),
    path('entry/<int:pk>/edit/', views.timesheet_entry_edit, name='entry_edit'),
    path('entry/<int:pk>/delete/', views.timesheet_entry_delete, name='entry_delete'),
    path('export/', views.timesheet_export, name='export'),
    path('hr/request/', views.hr_request_timesheets, name='hr_request'),
    path('send/<int:request_id>/', views.send_to_hr, name='send_to_hr'),
    path('send/<int:request_id>/ack/', views.acknowledge_send, name='acknowledge_send'),
    path('hr/request/<int:request_id>/', views.hr_request_detail, name='hr_request_detail'),
    path('hr/request/<int:request_id>/delete/', views.hr_request_delete, name='hr_request_delete'),
]