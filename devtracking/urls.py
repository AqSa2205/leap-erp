from django.urls import path

from . import views

app_name = 'devtracking'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('assign/', views.TaskAssignView.as_view(), name='assign'),
    path('tasks/', views.TaskListView.as_view(), name='tasks'),
    path('developer/<int:pk>/', views.DevDetailView.as_view(), name='dev_detail'),
    path('my-tasks/', views.MyTasksView.as_view(), name='my_tasks'),
    path('tasks/<int:pk>/action/', views.task_action, name='task_action'),
    path('generate/', views.generate_now, name='generate_now'),
]
