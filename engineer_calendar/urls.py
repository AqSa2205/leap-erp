# engineer_calendar/urls.py
from django.urls import path
from . import views

app_name = 'engineer_calendar'

urlpatterns = [
    path('', views.calendar_grid, name='grid'),
    path('generate-draft/', views.generate_draft_view, name='generate_draft'),
    path('cell/save/', views.save_cell, name='save_cell'),
    path('export/', views.export_excel, name='export_excel'),
]