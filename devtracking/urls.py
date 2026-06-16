from django.urls import path

from . import views

app_name = 'devtracking'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('assign/', views.TaskAssignView.as_view(), name='assign'),
    path('tasks/bulk/', views.bulk_create, name='bulk_create'),
    path('backlog/', views.BacklogListView.as_view(), name='backlog'),
    path('tasks/<int:pk>/assign/', views.assign_existing, name='assign_existing'),
    path('tasks/<int:pk>/edit/', views.TaskEditView.as_view(), name='task_edit'),
    path('tasks/<int:pk>/delete/', views.TaskDeleteView.as_view(), name='task_delete'),
    path('tasks/', views.TaskListView.as_view(), name='tasks'),
    path('developer/<int:pk>/', views.DevDetailView.as_view(), name='dev_detail'),
    path('my-tasks/', views.MyTasksView.as_view(), name='my_tasks'),
    path('tasks/<int:pk>/action/', views.task_action, name='task_action'),
    path('tasks/<int:pk>/refresh-github/', views.refresh_github, name='refresh_github'),
    path('generate/', views.generate_now, name='generate_now'),
]
