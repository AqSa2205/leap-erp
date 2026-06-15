from django.urls import path

from . import views

app_name = 'devtracking'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('assign/', views.TaskAssignView.as_view(), name='assign'),
    path('tasks/', views.TaskListView.as_view(), name='tasks'),
    path('developer/<int:pk>/', views.DevDetailView.as_view(), name='dev_detail'),
    path('my-tasks/', views.my_tasks_stub, name='my_tasks'),  # Task-4 stub
]
