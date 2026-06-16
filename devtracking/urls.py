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
    path('stacks/', views.StackListView.as_view(), name='stacks'),
    path('stacks/create/', views.stack_create, name='stack_create'),
    path('stacks/group/', views.stack_group, name='stack_group'),
    path('stacks/<int:pk>/', views.StackDetailView.as_view(), name='stack_detail'),
    path('stacks/<int:pk>/assign/', views.stack_assign, name='stack_assign'),
    path('stacks/<int:pk>/add-tasks/', views.stack_add_tasks, name='stack_add_tasks'),
    path('stacks/<int:pk>/delete/', views.StackDeleteView.as_view(), name='stack_delete'),
]
