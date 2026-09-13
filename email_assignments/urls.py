from django.urls import path

from . import views

app_name = 'email_assignments'

urlpatterns = [
    path('', views.mailbox_list, name='list'),
    path('assign/', views.assign_mailbox, name='assign'),
    path('<int:pk>/toggle/', views.toggle_mailbox, name='toggle'),
    path('<int:pk>/delete/', views.delete_mailbox, name='delete'),
    path('department-lock/<str:department>/toggle/', views.toggle_department_lock, name='toggle_department_lock'),
]
