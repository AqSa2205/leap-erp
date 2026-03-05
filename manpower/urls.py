from django.urls import path
from . import views

app_name = 'manpower'

urlpatterns = [
    path('', views.SheetListView.as_view(), name='sheet_list'),
    path('create/', views.SheetCreateView.as_view(), name='sheet_create'),
    path('new-sheet/', views.NewSheetView.as_view(), name='sheet_create_new'),
    path('import/', views.sheet_import, name='sheet_import'),
    path('<int:pk>/', views.SheetDetailView.as_view(), name='sheet_detail'),
    path('<int:pk>/edit/', views.SheetUpdateView.as_view(), name='sheet_update'),
    path('<int:pk>/delete/', views.SheetDeleteView.as_view(), name='sheet_delete'),
    path('<int:pk>/export/', views.sheet_export, name='sheet_export'),
    path('<int:pk>/add-item/', views.LineItemCreateView.as_view(), name='lineitem_create'),
    path('item/<int:pk>/edit/', views.LineItemUpdateView.as_view(), name='lineitem_update'),
    path('item/<int:pk>/delete/', views.LineItemDeleteView.as_view(), name='lineitem_delete'),
]
