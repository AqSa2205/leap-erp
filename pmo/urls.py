from django.urls import path

from . import views

app_name = 'pmo'

urlpatterns = [
    path('', views.board, name='board'),
    path('project/<int:pk>/', views.project_detail, name='project_detail'),
    path('milestone/<int:pk>/progress/', views.update_progress, name='update_progress'),
]
