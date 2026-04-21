from django.urls import path
from . import views
from .search import global_search

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('api/chart-data/', views.chart_data, name='chart_data'),
    path('api/search/', global_search, name='global_search'),
]
