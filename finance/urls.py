from django.urls import path

from . import views

app_name = 'finance'

urlpatterns = [
    path('', views.finance_home, name='home'),
    path('project/<int:project_pk>/schedule/', views.project_schedule, name='schedule'),
    path('project/<int:project_pk>/cash-outflow/', views.project_cash_outflow, name='cash_outflow'),
    path('approve-margin/<int:sheet_pk>/<str:key>/', views.approve_margin, name='approve_margin'),
]
