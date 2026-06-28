from django.contrib import admin
from .models import (
    TechnicalProposal, EngineeringDocument, ProposalBoilerplate,
    SectionHeading, ProposalSection,
)


class EngineeringDocumentInline(admin.TabularInline):
    model = EngineeringDocument
    extra = 1


class ProposalSectionInline(admin.TabularInline):
    model = ProposalSection
    extra = 0


@admin.register(SectionHeading)
class SectionHeadingAdmin(admin.ModelAdmin):
    """The admin-editable library of section headings users pick from."""
    list_display = ['name', 'order', 'is_active', 'created_at']
    list_editable = ['order', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(TechnicalProposal)
class TechnicalProposalAdmin(admin.ModelAdmin):
    list_display = ['title', 'proposal_reference', 'client_name', 'status', 'updated_at']
    list_filter = ['status', 'region_entity']
    search_fields = ['title', 'proposal_reference', 'client_name']
    inlines = [ProposalSectionInline, EngineeringDocumentInline]


@admin.register(ProposalBoilerplate)
class ProposalBoilerplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'section', 'created_at']
    list_filter = ['section']
