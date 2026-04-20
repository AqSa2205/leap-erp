from django.urls import path
from django.contrib.auth.decorators import login_required
from . import views

app_name = 'proposals'

urlpatterns = [
    # Proposals
    path('', views.ProposalListView.as_view(), name='list'),
    path('create/', views.ProposalCreateView.as_view(), name='create'),
    path('<int:pk>/', views.ProposalDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.ProposalUpdateView.as_view(), name='edit'),
    path('<int:pk>/content/', views.ProposalEditContentView.as_view(), name='content'),
    path('<int:pk>/delete/', views.ProposalDeleteView.as_view(), name='delete'),
    path('<int:pk>/export-docx/', login_required(views.proposal_export_docx), name='export_docx'),
    path('<int:pk>/save-section/', views.ajax_save_section, name='save_section'),
    path('<int:pk>/upload-image/', views.ajax_upload_image, name='upload_image'),

    # Prequalification Documents (PQD)
    path('pqd/', views.PQDListView.as_view(), name='pqd_list'),
    path('pqd/create/', views.PQDCreateView.as_view(), name='pqd_create'),
    path('pqd/<int:pk>/', views.PQDDetailView.as_view(), name='pqd_detail'),
    path('pqd/<int:pk>/edit/', views.PQDUpdateView.as_view(), name='pqd_edit'),
    path('pqd/<int:pk>/content/', views.PQDEditContentView.as_view(), name='pqd_content'),
    path('pqd/<int:pk>/delete/', views.PQDDeleteView.as_view(), name='pqd_delete'),
    path('pqd/<int:pk>/save-section/', views.ajax_save_pqd_section, name='pqd_save_section'),
    path('pqd/<int:pk>/save-cover-field/', views.ajax_save_pqd_cover_field, name='pqd_save_cover_field'),
    path('pqd/<int:pk>/export/', views.pqd_export, name='pqd_export'),
    path('pqd/<int:pk>/upload/', views.ajax_pqd_upload_attachment, name='pqd_upload'),
    path('pqd/attachment/<int:pk>/delete/', views.ajax_pqd_delete_attachment, name='pqd_delete_attachment'),

    # Boilerplate
    path('boilerplate/', views.BoilerplateListView.as_view(), name='boilerplate_list'),
    path('boilerplate/create/', views.BoilerplateCreateView.as_view(), name='boilerplate_create'),
    path('boilerplate/<int:pk>/edit/', views.BoilerplateUpdateView.as_view(), name='boilerplate_edit'),
    path('boilerplate/<int:pk>/delete/', views.BoilerplateDeleteView.as_view(), name='boilerplate_delete'),
    path('boilerplate/<int:pk>/content/', login_required(views.ajax_load_boilerplate), name='boilerplate_content'),
]
