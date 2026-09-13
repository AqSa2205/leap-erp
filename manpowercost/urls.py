from django.urls import path

from . import views

app_name = 'manpowercost'

urlpatterns = [
    path('', views.sheet_list, name='sheet_list'),
    path('rates/', views.rate_card, name='rate_card'),
    path('rates/<int:pk>/override/', views.rate_override, name='rate_override'),
    path('import/', views.sheet_import, name='sheet_import'),
    path('import/apply/', views.sheet_import_apply, name='sheet_import_apply'),
    path('<int:pk>/', views.sheet_detail, name='sheet_detail'),
]
