from django import forms
from .models import Project, Region, ProjectStatus, Document


class ProjectForm(forms.ModelForm):
    """Form for creating/editing projects"""

    class Meta:
        model = Project
        fields = [
            'project_name', 'proposal_reference', 'client_rfq_reference',
            'po_number', 'submission_deadline', 'estimated_po_date',
            'owner', 'epc', 'status', 'region', 'year', 'estimated_value',
            'actual_sales', 'po_award_quarter', 'success_quotient', 'minimum_achievement',
            'contact_with', 'remarks', 'notes', 'portal_url'
        ]
        widgets = {
            'submission_deadline': forms.DateInput(attrs={'type': 'date'}),
            'estimated_po_date': forms.DateInput(attrs={'type': 'date'}),
            'remarks': forms.Textarea(attrs={'rows': 3}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'year': forms.Select(choices=[('', 'Select Year')] + Project.YEAR_CHOICES),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'

        # Filter owner choices based on user role
        if self.user:
            from accounts.models import User
            if self.user.is_admin_user:
                self.fields['owner'].queryset = User.objects.filter(is_active=True)
            elif self.user.is_manager_user:
                self.fields['owner'].queryset = User.objects.filter(
                    is_active=True,
                    region=self.user.region
                )
            else:
                self.fields['owner'].queryset = User.objects.filter(pk=self.user.pk)


class ProjectFilterForm(forms.Form):
    """Form for filtering projects"""

    # Consolidated region choices
    REGION_CHOICES = [
        ('', 'All Regions'),
        ('LNUK', 'LNUK - Leap Networks UK & Global'),
        ('LNA', 'LNA - Leap Networks Arabia'),
        ('PA', 'PA - Pace Arabia'),
    ]

    # Year choices - generate dynamically
    import datetime
    current_year = datetime.datetime.now().year
    YEAR_CHOICES = [('', 'All Years')] + [(str(y), str(y)) for y in range(current_year + 2, 2019, -1)]

    search = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Search projects...'
    }))
    region = forms.ChoiceField(
        choices=REGION_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    year = forms.ChoiceField(
        choices=YEAR_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    status = forms.ModelChoiceField(
        queryset=ProjectStatus.objects.filter(is_active=True),
        required=False,
        empty_label='All Statuses',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    category = forms.ChoiceField(
        choices=[('', 'All Categories')] + ProjectStatus.CATEGORY_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    quarter = forms.ChoiceField(
        choices=[('', 'All Quarters')] + Project.QUARTER_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class DocumentForm(forms.ModelForm):
    """Form for uploading documents"""

    class Meta:
        model = Document
        fields = [
            'name', 'document_type', 'description', 'file',
            'project', 'reference_number', 'vendor_name',
            'document_date', 'expiry_date'
        ]
        widgets = {
            'document_date': forms.DateInput(attrs={'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs['class'] = 'form-control'

        # Make project optional in the form
        self.fields['project'].required = False
        self.fields['project'].empty_label = 'No Project (Standalone Document)'

        # Add helpful placeholders
        self.fields['name'].widget.attrs['placeholder'] = 'e.g., Vendor Quote - ABC Supplies'
        self.fields['reference_number'].widget.attrs['placeholder'] = 'e.g., QT-2026-001'
        self.fields['vendor_name'].widget.attrs['placeholder'] = 'e.g., ABC Supplies Ltd'


class DocumentFilterForm(forms.Form):
    """Form for filtering documents"""

    search = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Search documents...'
    }))
    document_type = forms.ChoiceField(
        choices=[('', 'All Types')] + Document.DOCUMENT_TYPE_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by('-created_at'),
        required=False,
        empty_label='All Projects',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
