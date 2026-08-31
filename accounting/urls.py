from django.urls import path

from . import views

app_name = 'accounting'

urlpatterns = [
    path('chart/', views.chart_of_accounts, name='chart'),
    path('chart/import/', views.chart_import, name='chart_import'),
    path('chart/import/apply/', views.chart_import_apply, name='chart_import_apply'),
    path('chart/<str:code>/', views.account_detail, name='account_detail'),
    path('ledger/<str:code>/', views.account_ledger, name='account_ledger'),
    path('zoho/mapping/', views.zoho_mapping, name='zoho_mapping'),
    path('zoho/mapping/save/', views.zoho_mapping_save, name='zoho_mapping_save'),
    path('zoho/mapping/apply-certain/', views.zoho_mapping_apply_certain,
         name='zoho_mapping_apply_certain'),
]
