from django.urls import path

from . import views

app_name = 'kpis'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('manage/', views.manage, name='manage'),
]
