from django.urls import path

from . import views

app_name = 'kpis'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('people/', views.people, name='people'),
    path('manage/', views.manage, name='manage'),
    path('activity/', views.activity_overview, name='activity'),
    path('activity/<int:user_id>/', views.activity_detail, name='activity_detail'),
    path('new/', views.kpi_new, name='kpi_new'),
]
