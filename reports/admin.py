from django.contrib import admin
from .models import Vendor, EPC, Exhibition, ProcurementPortal, Certification, SalesContact


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ['name', 'vendor_type', 'contact_person', 'is_active', 'created_at']
    list_filter = ['vendor_type', 'is_active']
    search_fields = ['name', 'contact_person', 'products_services']
    ordering = ['name']


@admin.register(EPC)
class EPCAdmin(admin.ModelAdmin):
    list_display = ['name', 'region', 'contact_person', 'is_active', 'created_at']
    list_filter = ['is_active', 'region']
    search_fields = ['name', 'contact_person', 'specialization']
    ordering = ['name']


@admin.register(Exhibition)
class ExhibitionAdmin(admin.ModelAdmin):
    list_display = ['name', 'year', 'location', 'leads_generated', 'is_attended']
    list_filter = ['year', 'is_attended']
    search_fields = ['name', 'location']
    ordering = ['-year', 'name']


@admin.register(ProcurementPortal)
class ProcurementPortalAdmin(admin.ModelAdmin):
    list_display = ['name', 'registration_type', 'registration_date', 'expiry_date', 'is_active']
    list_filter = ['registration_type', 'is_active']
    search_fields = ['name']
    ordering = ['name']


@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = ['name', 'issuing_body', 'status', 'issue_date', 'expiry_date']
    list_filter = ['status']
    search_fields = ['name', 'issuing_body']
    ordering = ['name']


@admin.register(SalesContact)
class SalesContactAdmin(admin.ModelAdmin):
    list_display = ['company_name', 'contact_name', 'category', 'is_contacted', 'last_contact_date']
    list_filter = ['category', 'is_contacted']
    search_fields = ['company_name', 'contact_name']
    ordering = ['company_name']
