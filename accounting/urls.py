from django.urls import path

from . import views

app_name = 'accounting'

urlpatterns = [
    path('chart/', views.chart_of_accounts, name='chart'),
    path('chart/<str:code>/', views.account_detail, name='account_detail'),
]
