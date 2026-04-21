from django.urls import path
from django.contrib.auth.decorators import login_required
from . import views

app_name = 'costing'

urlpatterns = [
    # Costing sheets
    path('', views.CostingListView.as_view(), name='list'),
    path('create/', views.CostingCreateView.as_view(), name='create'),
    path('import/', login_required(views.costing_import_new), name='import_new'),
    path('<int:pk>/', views.CostingDetailView.as_view(), name='detail'),
    path('<int:pk>/edit/', views.CostingUpdateView.as_view(), name='edit'),
    path('<int:pk>/delete/', views.CostingDeleteView.as_view(), name='delete'),
    path('<int:pk>/export/', login_required(views.costing_export_excel), name='export'),
    path('<int:pk>/export-pdf/', login_required(views.costing_export_pdf), name='export_pdf'),
    path('<int:pk>/import/', login_required(views.costing_import_excel), name='import_excel'),
    path('<int:pk>/update-params/', views.ajax_update_sheet_params, name='update_params'),
    path('exchange-rates/<int:pk>/update/', views.ajax_update_exchange_rate, name='exchange_rate_update'),

    # Sections
    path('<int:sheet_pk>/add-section/', views.SectionCreateView.as_view(), name='section_create'),
    path('section/<int:pk>/edit/', views.SectionUpdateView.as_view(), name='section_edit'),
    path('section/<int:pk>/delete/', views.SectionDeleteView.as_view(), name='section_delete'),
    path('section/<int:pk>/items/', views.ajax_section_items, name='section_items'),
    path('section/<int:pk>/update-rate/', views.ajax_update_section_rate, name='section_update_rate'),
    path('section/<int:pk>/bulk-delete/', views.bulk_delete_page, name='bulk_delete_page'),
    path('section/<int:pk>/add-line-item/', views.ajax_add_line_item, name='ajax_add_line_item'),
    path('section/<int:pk>/paste-line-items/', views.ajax_paste_line_items, name='ajax_paste_line_items'),

    # Line items
    path('section/<int:section_pk>/add-item/', views.LineItemCreateView.as_view(), name='item_create'),
    path('item/<int:pk>/edit/', views.LineItemUpdateView.as_view(), name='item_edit'),
    path('item/<int:pk>/delete/', views.LineItemDeleteView.as_view(), name='item_delete'),
    path('item/<int:pk>/update-margin/', views.ajax_update_item_margin, name='item_update_margin'),
    path('item/<int:pk>/update-field/', views.ajax_update_item_field, name='item_update_field'),

    # Exchange rates
    path('exchange-rates/', views.ExchangeRateListView.as_view(), name='exchange_rates'),
    path('exchange-rates/add/', views.ExchangeRateCreateView.as_view(), name='exchange_rate_create'),
    path('exchange-rates/<int:pk>/edit/', views.ExchangeRateUpdateView.as_view(), name='exchange_rate_edit'),
    path('exchange-rates/<int:pk>/delete/', views.ExchangeRateDeleteView.as_view(), name='exchange_rate_delete'),

    # Terms templates
    path('terms-templates/', views.TermsTemplateListView.as_view(), name='terms_templates'),
    path('terms-templates/create/', views.TermsTemplateCreateView.as_view(), name='terms_template_create'),
    path('terms-templates/<int:pk>/edit/', views.TermsTemplateUpdateView.as_view(), name='terms_template_edit'),
    path('terms-templates/<int:pk>/delete/', views.TermsTemplateDeleteView.as_view(), name='terms_template_delete'),

    # Toggle terms on costing sheet
    path('<int:pk>/toggle-term/', views.ajax_toggle_term, name='toggle_term'),

    # Scope of Work items
    path('<int:pk>/add-sow-item/', views.ajax_add_sow_item, name='add_sow_item'),
    path('sow-item/<int:pk>/delete/', views.ajax_delete_sow_item, name='delete_sow_item'),

    # PDF revisions
    path('revision/<int:pk>/delete/', login_required(views.delete_costing_revision), name='delete_revision'),

    # Workflow stage transitions (handover BOM → costing → finalize)
    path('<int:pk>/workflow/', views.costing_workflow_transition, name='workflow_transition'),

    # Bulk-apply the sheet's default supplier currency to every line item
    path('<int:pk>/apply-default-currency/', views.ajax_apply_default_currency, name='apply_default_currency'),
]
