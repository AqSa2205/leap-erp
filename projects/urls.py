from django.urls import path
from . import views

app_name = 'projects'

urlpatterns = [
    # Project URLs
    path('', views.ProjectListView.as_view(), name='list'),
    path('recover/', views.project_recovery, name='recover'),
    path('print/pdf/', views.pipeline_print_pdf, name='print_pdf'),
    path('create/', views.ProjectCreateView.as_view(), name='create'),
    path('<int:pk>/', views.ProjectDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.ProjectUpdateView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.ProjectDeleteView.as_view(), name='delete'),
    path('recycle-bin/', views.ProjectRecycleBinView.as_view(), name='recycle_bin'),
    path('<int:pk>/restore/', views.project_restore, name='restore'),
    path('import/', views.ProjectImportView.as_view(), name='import_projects'),
    path('<int:pk>/add-document/', views.add_project_document, name='add_document'),
    path('<int:pk>/add-documents-bulk/', views.add_project_documents_bulk, name='add_documents_bulk'),
    path('<int:pk>/link-email/', views.link_pipeline_email, name='link_pipeline_email'),
    # message_id/attachment_id ride as ?query params here, not path segments
    # — Graph ids can contain characters (e.g. '/') that <str:> rejects.
    # See _add_attachment_urls() in views.py.
    path('<int:pk>/link-email/attachment/',
         views.view_pipeline_inbox_attachment, name='view_pipeline_inbox_attachment'),
    path('<int:pk>/delink-email/', views.delink_pipeline_email, name='delink_pipeline_email'),
    path('link-email/new/', views.link_pipeline_email_new, name='link_pipeline_email_new'),
    path('link-email/new/attachment/',
         views.view_pipeline_inbox_attachment, name='view_pipeline_inbox_attachment_new'),
    path('next-reference/', views.next_lna_reference_preview, name='next_reference_preview'),

    # Revisions
    path('<int:pk>/revisions/create/', views.ProjectRevisionCreateView.as_view(), name='revision_create'),
    path('<int:pk>/revisions/<int:revision_pk>/', views.ProjectRevisionDetailView.as_view(), name='revision_detail'),

    # Document URLs
    path('documents/', views.DocumentListView.as_view(), name='document_list'),
    path('documents/upload/', views.DocumentCreateView.as_view(), name='document_create'),
    path('documents/<int:pk>/', views.DocumentDetailView.as_view(), name='document_detail'),
    path('documents/<int:pk>/edit/', views.DocumentUpdateView.as_view(), name='document_edit'),
    path('documents/<int:pk>/delete/', views.DocumentDeleteView.as_view(), name='document_delete'),
]
