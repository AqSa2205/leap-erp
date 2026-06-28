from django import forms
from django.forms import inlineformset_factory
from .models import (
    TechnicalProposal, EngineeringDocument, ProposalBoilerplate,
    PrequalificationDocument, ProposalSection, SectionHeading,
)
from projects.models import Project


class ProposalSectionForm(forms.ModelForm):
    class Meta:
        model = ProposalSection
        fields = ['heading', 'content', 'order']
        widgets = {
            'heading': forms.TextInput(attrs={
                'class': 'form-control fw-bold', 'list': 'section-heading-options',
                'placeholder': 'Section heading'}),
            'content': forms.Textarea(attrs={'class': 'tinymce-editor'}),
            'order': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm section-order', 'style': 'width:70px'}),
        }


ProposalSectionFormSet = inlineformset_factory(
    TechnicalProposal, ProposalSection, form=ProposalSectionForm,
    extra=0, can_delete=True,
)


class ProposalMetadataForm(forms.ModelForm):
    class Meta:
        model = TechnicalProposal
        fields = [
            'title', 'project', 'proposal_reference', 'document_type',
            'client_name', 'project_description', 'region_entity',
            'revision', 'revision_date',
            'prepared_by_initials', 'checked_by_initials', 'approved_by_initials',
            'status',
        ]
        widgets = {
            'revision_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['project'].required = False
        self.fields['project'].queryset = Project.objects.select_related('region').all()

        if self.user and not self.user.is_super_admin_user:
            self.fields['project'].queryset = Project.objects.filter(
                region=self.user.region
            ).select_related('region')


class ProposalContentForm(forms.ModelForm):
    class Meta:
        model = TechnicalProposal
        fields = [
            'covering_letter', 'executive_summary', 'company_overview',
            'understanding_of_requirements', 'proposed_technical_solution',
            'delivery_implementation', 'risk_management', 'service_management',
            'data_protection', 'assumptions_constraints',
        ]
        widgets = {
            'covering_letter': forms.Textarea(attrs={'rows': 12}),
            'executive_summary': forms.Textarea(attrs={'rows': 12}),
            'company_overview': forms.Textarea(attrs={'rows': 12}),
            'understanding_of_requirements': forms.Textarea(attrs={'rows': 12}),
            'proposed_technical_solution': forms.Textarea(attrs={'rows': 12}),
            'delivery_implementation': forms.Textarea(attrs={'rows': 12}),
            'risk_management': forms.Textarea(attrs={'rows': 12}),
            'service_management': forms.Textarea(attrs={'rows': 12}),
            'data_protection': forms.Textarea(attrs={'rows': 12}),
            'assumptions_constraints': forms.Textarea(attrs={'rows': 12}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


EngineeringDocumentFormSet = inlineformset_factory(
    TechnicalProposal,
    EngineeringDocument,
    fields=['doc_type', 'doc_number', 'doc_title', 'order'],
    extra=1,
    can_delete=True,
    widgets={
        'doc_type': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Type'}),
        'doc_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Number'}),
        'doc_title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Title'}),
        'order': forms.NumberInput(attrs={'class': 'form-control', 'style': 'width:80px'}),
    },
)


class ProposalFilterForm(forms.Form):
    search = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Search proposals...',
    }))
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Statuses')] + TechnicalProposal.STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class PQDMetadataForm(forms.ModelForm):
    class Meta:
        model = PrequalificationDocument
        fields = [
            'title', 'project', 'pqd_reference', 'document_type',
            'client_name', 'project_description', 'region_entity',
            'revision', 'revision_date',
            'prepared_by_initials', 'checked_by_initials', 'approved_by_initials',
            'status',
        ]
        widgets = {
            'revision_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['project'].required = False
        self.fields['project'].queryset = Project.objects.select_related('region').all()

        if self.user and not self.user.is_super_admin_user:
            self.fields['project'].queryset = Project.objects.filter(
                region=self.user.region
            ).select_related('region')


class PQDFilterForm(forms.Form):
    search = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control', 'placeholder': 'Search PQDs...',
    }))
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Statuses')] + PrequalificationDocument.STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class ProposalBoilerplateForm(forms.ModelForm):
    class Meta:
        model = ProposalBoilerplate
        fields = ['name', 'section', 'content']
        widgets = {
            'content': forms.Textarea(attrs={'rows': 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
