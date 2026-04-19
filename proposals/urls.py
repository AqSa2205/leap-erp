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

    # Boilerplate
    path('boilerplate/', views.BoilerplateListView.as_view(), name='boilerplate_list'),
    path('boilerplate/create/', views.BoilerplateCreateView.as_view(), name='boilerplate_create'),
    path('boilerplate/<int:pk>/edit/', views.BoilerplateUpdateView.as_view(), name='boilerplate_edit'),
    path('boilerplate/<int:pk>/delete/', views.BoilerplateDeleteView.as_view(), name='boilerplate_delete'),
    path('boilerplate/<int:pk>/content/', login_required(views.ajax_load_boilerplate), name='boilerplate_content'),
]
