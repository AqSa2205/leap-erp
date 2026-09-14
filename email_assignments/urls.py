from django.urls import path

from . import views

app_name = 'email_assignments'

urlpatterns = [
    path('', views.mailbox_list, name='list'),
    path('department-lock/<str:department>/toggle/', views.toggle_department_lock, name='toggle_department_lock'),

    # Technical Proposals tab
    path('proposals/assign/', views.assign_proposal_mailbox, name='assign_proposal'),
    path('proposals/<int:pk>/toggle/', views.toggle_proposal_mailbox, name='toggle_proposal'),
    path('proposals/<int:pk>/delete/', views.delete_proposal_mailbox, name='delete_proposal'),

    # Costing tab
    path('costing/assign/', views.assign_revision_mailbox, name='assign_revision'),
    path('costing/<int:pk>/toggle/', views.toggle_revision_mailbox, name='toggle_revision'),
    path('costing/<int:pk>/delete/', views.delete_revision_mailbox, name='delete_revision'),

    # Commercial Pipeline tab
    path('pipeline/assign/', views.assign_monitored_mailbox, name='assign_monitored'),
    path('pipeline/<int:pk>/toggle/', views.toggle_monitored_mailbox, name='toggle_monitored'),
    path('pipeline/<int:pk>/delete/', views.delete_monitored_mailbox, name='delete_monitored'),
]
