from django.urls import path
from . import views
from .search import global_search
from .my_work import my_work

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('my-work/', my_work, name='my_work'),
    path('api/chart-data/', views.chart_data, name='chart_data'),
    path('api/search/', global_search, name='global_search'),
    path('storage/', views.storage_report, name='storage_report'),
    path('storage/preview/', views.storage_orphan_preview, name='storage_orphan_preview'),
]
