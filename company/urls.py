from django.urls import path

from . import views

app_name = 'company'

urlpatterns = [
    path('documents/', views.CompanyDocumentListView.as_view(), name='document_list'),
    path('documents/upload/', views.company_document_upload, name='document_upload'),
    path('documents/<int:pk>/edit/', views.company_document_edit, name='document_edit'),
    path('documents/<int:pk>/delete/', views.company_document_delete, name='document_delete'),
]
