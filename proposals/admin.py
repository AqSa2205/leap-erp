from django.contrib import admin
from tinymce.widgets import TinyMCE
from django import forms
from .models import (
    TechnicalProposal, EngineeringDocument, ProposalBoilerplate,
    SectionHeading, ProposalSection,
    PrequalLibraryItem, PrequalSubmission,
    SectionHeadingTemplate,
)


@admin.register(PrequalLibraryItem)
class PrequalLibraryItemAdmin(admin.ModelAdmin):
    """The shared 25-PDF prequalification library — upload each heading's PDF."""
    list_display = ['order', 'heading', 'has_pdf', 'is_active', 'updated_at']
    list_editable = ['order', 'is_active']
    list_display_links = ['heading']
    list_filter = ['is_active']
    search_fields = ['heading']

    @admin.display(boolean=True, description='PDF')
    def has_pdf(self, obj):
        return bool(obj.pdf)


@admin.register(PrequalSubmission)
class PrequalSubmissionAdmin(admin.ModelAdmin):
    list_display = ['title', 'project', 'client_name', 'created_by', 'updated_at']
    search_fields = ['title', 'client_name', 'reference']
    filter_horizontal = ['selected_items']


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


class SectionHeadingTemplateForm(forms.ModelForm):
    class Meta:
        model = SectionHeadingTemplate
        fields = '__all__'
        widgets = {'content': TinyMCE(attrs={'cols': 100, 'rows': 25}, mce_attrs={
            'plugins': 'code table lists image link',
            'toolbar': 'undo redo | blocks | bold italic underline | '
                       'bullist numlist | table | image | code',
            'menubar': False,
            # Match the main proposal editor: keep image/link URLs
            # root-relative, don't rewrite them relative to this admin
            # page's own (nested) URL.
            'relative_urls': False,
            'remove_script_host': True,
            'document_base_url': '/',
        })}


@admin.register(SectionHeadingTemplate)
class SectionHeadingTemplateAdmin(admin.ModelAdmin):
    """Where the AI/Telecom/Procurement template content is authored per
    heading. This is the admin screen your manager/team lead uses to paste
    in each department's composed section content (with images)."""
    form = SectionHeadingTemplateForm
    list_display = ['heading', 'department', 'updated_at']
    list_filter = ['department', 'heading']
    search_fields = ['heading__name']
