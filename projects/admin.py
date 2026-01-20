from django.contrib import admin
from .models import Region, ProjectStatus, Project, ProjectHistory, Document


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'currency', 'is_active', 'created_at']
    list_filter = ['is_active', 'currency']
    search_fields = ['name', 'code']


@admin.register(ProjectStatus)
class ProjectStatusAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'color', 'order', 'is_active']
    list_filter = ['category', 'is_active']
    search_fields = ['name']
    ordering = ['order', 'name']


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = [
        'proposal_reference', 'project_name', 'region', 'status',
        'estimated_value', 'owner', 'submission_deadline', 'created_at'
    ]
    list_filter = ['region', 'status', 'po_award_quarter', 'created_at']
    search_fields = ['project_name', 'proposal_reference', 'client_rfq_reference']
    date_hierarchy = 'created_at'
    raw_id_fields = ['owner', 'created_by']

    fieldsets = (
        ('Basic Information', {
            'fields': (
                'serial_number', 'project_name', 'proposal_reference',
                'client_rfq_reference', 'po_number'
            )
        }),
        ('Status & Region', {
            'fields': ('status', 'region', 'owner', 'epc')
        }),
        ('Dates', {
            'fields': ('submission_deadline', 'estimated_po_date')
        }),
        ('Financial', {
            'fields': (
                'estimated_value', 'po_award_quarter',
                'success_quotient', 'minimum_achievement'
            )
        }),
        ('Additional Info', {
            'fields': ('contact_with', 'remarks', 'notes', 'portal_url'),
            'classes': ('collapse',)
        }),
    )


@admin.register(ProjectHistory)
class ProjectHistoryAdmin(admin.ModelAdmin):
    list_display = ['project', 'old_status', 'new_status', 'changed_by', 'changed_at']
    list_filter = ['old_status', 'new_status', 'changed_at']
    search_fields = ['project__project_name', 'project__proposal_reference']
    date_hierarchy = 'changed_at'


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'document_type', 'project', 'vendor_name',
        'uploaded_by', 'uploaded_at', 'file_size_display'
    ]
    list_filter = ['document_type', 'uploaded_at']
    search_fields = ['name', 'reference_number', 'vendor_name', 'project__proposal_reference']
    date_hierarchy = 'uploaded_at'
    raw_id_fields = ['project', 'uploaded_by']

    fieldsets = (
        ('Document Information', {
            'fields': ('name', 'document_type', 'description', 'file')
        }),
        ('Project Association', {
            'fields': ('project',)
        }),
        ('Metadata', {
            'fields': ('reference_number', 'vendor_name', 'document_date', 'expiry_date')
        }),
    )

    readonly_fields = ['file_size', 'uploaded_by', 'uploaded_at']

    def save_model(self, request, obj, form, change):
        if not change:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)
