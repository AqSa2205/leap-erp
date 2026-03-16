from django.contrib import admin
from .models import TechnicalProposal, EngineeringDocument, ProposalBoilerplate


class EngineeringDocumentInline(admin.TabularInline):
    model = EngineeringDocument
    extra = 1


@admin.register(TechnicalProposal)
class TechnicalProposalAdmin(admin.ModelAdmin):
    list_display = ['title', 'proposal_reference', 'client_name', 'status', 'updated_at']
    list_filter = ['status', 'region_entity']
    search_fields = ['title', 'proposal_reference', 'client_name']
    inlines = [EngineeringDocumentInline]


@admin.register(ProposalBoilerplate)
class ProposalBoilerplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'section', 'created_at']
    list_filter = ['section']
