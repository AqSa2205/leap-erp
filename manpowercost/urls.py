from django.urls import path

from . import views

app_name = 'manpowercost'

urlpatterns = [
    path('', views.sheet_list, name='sheet_list'),
    path('rates/', views.rate_card, name='rate_card'),
    path('rates/new/', views.rate_create, name='rate_create'),
    path('rates/<int:pk>/update/', views.rate_update, name='rate_update'),
    path('rates/<int:pk>/delete/', views.rate_delete, name='rate_delete'),
    path('basis/<int:pk>/update/', views.basis_update, name='basis_update'),
    path('<int:pk>/line/add/', views.line_add, name='line_add'),
    path('line/<int:pk>/update/', views.line_update, name='line_update'),
    path('line/<int:pk>/delete/', views.line_delete, name='line_delete'),
    path('sheet/new/', views.sheet_create, name='sheet_create'),
    path('<int:pk>/', views.sheet_detail, name='sheet_detail'),
]
