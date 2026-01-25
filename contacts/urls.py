from django.urls import path
from . import views

app_name = 'contacts'

urlpatterns = [
    # Main list view
    path('', views.ContactListView.as_view(), name='contact_list'),

    # Category-specific views
    path('category/<str:category>/', views.ContactByCategoryView.as_view(), name='contact_by_category'),

    # CRUD operations
    path('add/', views.ContactCreateView.as_view(), name='contact_create'),
    path('<int:pk>/', views.ContactDetailView.as_view(), name='contact_detail'),
    path('<int:pk>/edit/', views.ContactUpdateView.as_view(), name='contact_update'),
    path('<int:pk>/delete/', views.ContactDeleteView.as_view(), name='contact_delete'),

    # Import/Export
    path('import/', views.contact_import, name='contact_import'),
    path('export/', views.contact_export, name='contact_export'),
]
