from django.urls import path

from . import views

app_name = 'devtracking'

urlpatterns = [
    # Placeholder names so base.html nav resolves; replaced in Task 3/4.
    path('', views.dashboard, name='dashboard'),
    path('my-tasks/', views.my_tasks, name='my_tasks'),
]
