from django.urls import path
from . import views

app_name = 'hr'

urlpatterns = [
    path('', views.EmployeeListView.as_view(), name='employee_list'),
    path('create/', views.EmployeeCreateView.as_view(), name='employee_create'),
    path('import/', views.employee_import, name='employee_import'),
    path('export/', views.employee_export, name='employee_export'),
    path('<int:pk>/', views.EmployeeDetailView.as_view(), name='employee_detail'),
    path('<int:pk>/edit/', views.EmployeeUpdateView.as_view(), name='employee_update'),
    path('<int:pk>/delete/', views.EmployeeDeleteView.as_view(), name='employee_delete'),

    # Assets
    path('assets/', views.AssetListView.as_view(), name='asset_list'),
    path('assets/create/', views.AssetCreateView.as_view(), name='asset_create'),
    path('assets/import/', views.asset_import, name='asset_import'),
    path('assets/export/', views.asset_export, name='asset_export'),
    path('assets/<int:pk>/', views.AssetDetailView.as_view(), name='asset_detail'),
    path('assets/<int:pk>/edit/', views.AssetUpdateView.as_view(), name='asset_update'),
    path('assets/<int:pk>/delete/', views.AssetDeleteView.as_view(), name='asset_delete'),
]
