from django.urls import path

from . import views

app_name = 'manpowercost'

urlpatterns = [
    path('', views.sheet_list, name='sheet_list'),
    path('rates/', views.rate_card, name='rate_card'),
    path('rates/<int:pk>/override/', views.rate_override, name='rate_override'),
    path('<int:pk>/line/add/', views.line_add, name='line_add'),
    path('line/<int:pk>/update/', views.line_update, name='line_update'),
    path('line/<int:pk>/delete/', views.line_delete, name='line_delete'),
    path('sheet/new/', views.sheet_create, name='sheet_create'),
    path('<int:pk>/', views.sheet_detail, name='sheet_detail'),
]
