from django.urls import path
from . import views

app_name = 'procurement'

urlpatterns = [
    # Dashboard
    path('', views.procurement_dashboard, name='dashboard'),

    # Purchase Orders
    path('po/', views.POListView.as_view(), name='po_list'),
    path('po/create/', views.POCreateView.as_view(), name='po_create'),
    path('po/import/', views.po_import_excel, name='po_import'),
    path('po/<int:pk>/', views.PODetailView.as_view(), name='po_detail'),
    path('po/<int:pk>/edit/', views.POUpdateView.as_view(), name='po_update'),
    path('po/<int:pk>/delete/', views.PODeleteView.as_view(), name='po_delete'),
    path('po/<int:pk>/export/', views.po_export_excel, name='po_export'),
    path('po/<int:pk>/export-pdf/', views.po_export_pdf, name='po_export_pdf'),
    path('po/<int:pk>/toggle-term/', views.ajax_po_toggle_term, name='po_toggle_term'),

    # Procurement Summary
    path('summary/', views.SummaryListView.as_view(), name='summary_list'),
    path('summary/create/', views.SummaryCreateView.as_view(), name='summary_create'),
    path('summary/import/', views.summary_import_excel, name='summary_import'),
    path('summary/<int:pk>/', views.SummaryDetailView.as_view(), name='summary_detail'),
    path('summary/<int:pk>/edit/', views.SummaryUpdateView.as_view(), name='summary_update'),
    path('summary/<int:pk>/delete/', views.SummaryDeleteView.as_view(), name='summary_delete'),
    path('summary/<int:pk>/export/', views.summary_export_excel, name='summary_export'),
    path('summary/<int:pk>/export-pdf/', views.summary_export_pdf, name='summary_export_pdf'),

    # Delivery Notes
    path('dn/', views.DNListView.as_view(), name='dn_list'),
    path('dn/create/', views.DNCreateView.as_view(), name='dn_create'),
    path('dn/import/', views.dn_import_excel, name='dn_import'),
    path('dn/<int:pk>/', views.DNDetailView.as_view(), name='dn_detail'),
    path('dn/<int:pk>/edit/', views.DNUpdateView.as_view(), name='dn_update'),
    path('dn/<int:pk>/delete/', views.DNDeleteView.as_view(), name='dn_delete'),
    path('dn/<int:pk>/export/', views.dn_export_excel, name='dn_export'),
    path('dn/<int:pk>/export-pdf/', views.dn_export_pdf, name='dn_export_pdf'),

    # Inventory Reports
    path('inventory/', views.InventoryListView.as_view(), name='inventory_list'),
    path('inventory/create/', views.InventoryCreateView.as_view(), name='inventory_create'),
    path('inventory/import/', views.inventory_import_excel, name='inventory_import'),
    path('inventory/<int:pk>/', views.InventoryDetailView.as_view(), name='inventory_detail'),
    path('inventory/<int:pk>/edit/', views.InventoryUpdateView.as_view(), name='inventory_update'),
    path('inventory/<int:pk>/delete/', views.InventoryDeleteView.as_view(), name='inventory_delete'),
    path('inventory/<int:pk>/export/', views.inventory_export_excel, name='inventory_export'),
    path('inventory/<int:pk>/export-pdf/', views.inventory_export_pdf, name='inventory_export_pdf'),

    # FRC / PPE Uniform Tracker
    path('frc/', views.FRCListView.as_view(), name='frc_list'),
    path('frc/create/', views.FRCCreateView.as_view(), name='frc_create'),
    path('frc/import/', views.frc_import_excel, name='frc_import'),
    path('frc/<int:pk>/', views.FRCDetailView.as_view(), name='frc_detail'),
    path('frc/<int:pk>/edit/', views.FRCUpdateView.as_view(), name='frc_update'),
    path('frc/<int:pk>/delete/', views.FRCDeleteView.as_view(), name='frc_delete'),
    path('frc/<int:pk>/export/', views.frc_export_excel, name='frc_export'),
    path('frc/<int:pk>/export-pdf/', views.frc_export_pdf, name='frc_export_pdf'),

    # FRC Inventory (PPE Stock)
    path('frc-inventory/', views.frc_inventory_list, name='frc_inventory'),
    path('frc-inventory/add/', views.FRCInventoryCreateView.as_view(), name='frc_inventory_create'),
    path('frc-inventory/<int:pk>/edit/', views.FRCInventoryUpdateView.as_view(), name='frc_inventory_update'),
    path('frc-inventory/<int:pk>/delete/', views.FRCInventoryDeleteView.as_view(), name='frc_inventory_delete'),
]
