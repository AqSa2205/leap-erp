from django import forms
from .models import (
    Project, Region, ProjectStatus, Document,
    LNA_REFERENCE_RE, build_lna_reference, next_lna_reference_number,
    parse_lna_reference,
)


class ProjectForm(forms.ModelForm):
    """Form for creating/editing projects"""

    # Editable revision tag for LNA references, e.g. "R03" — appended to the
    # auto reference as "LNA #### - <name> (R03)". Not a model field; the
    # revision lives inside proposal_reference.
    lna_revision = forms.CharField(
        required=False, max_length=10,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. R03', 'data-lna-revision': '1'}),
        help_text='Revision tag (optional), e.g. R03. Leave blank for none.')

    class Meta:
        model = Project
        fields = [
            'project_name', 'proposal_reference', 'client_rfq_reference',
            'po_number', 'submission_deadline', 'estimated_po_date',
            'owner', 'customer', 'end_user', 'project_stage', 'status', 'region', 'year', 'estimated_value',
            'estimated_value_usd', 'estimated_value_per_annum', 'estimated_gp',
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

        instance = getattr(self, 'instance', None)
        is_create = not (instance and instance.pk)

        # On create, default the project region to the logged-in user's region.
        # Their region is already known, so an LNA user immediately gets the LNA
        # auto-reference without having to pick the region. (They can still
        # change it.)
        if is_create and self.user and getattr(self.user, 'region_id', None):
            self.initial['region'] = self.user.region_id

        # LNA references are auto-generated (LNA #### - project name), so the
        # field is optional here and read-only for LNA. JS keeps it in sync live
        # by region; this locks it server-side when the form opens in LNA mode
        # (LNA project being edited, or an LNA user's defaulted region on create).
        self.fields['proposal_reference'].required = False
        ref = self.fields['proposal_reference']
        ref.widget.attrs['data-lna-reference'] = '1'
        if instance and instance.pk and instance.region:
            effective_region = instance.region
        else:
            effective_region = getattr(self.user, 'region', None)
        if effective_region and getattr(effective_region, 'code', None) == 'LNA':
            ref.widget.attrs['readonly'] = 'readonly'

        # Seed the revision input from the existing reference (if any).
        if instance and instance.pk and not self.is_bound:
            parsed = parse_lna_reference(instance.proposal_reference or '')
            if parsed and parsed[1]:
                self.initial['lna_revision'] = parsed[1]

    def _is_lna(self, region):
        return bool(region and getattr(region, 'code', None) == 'LNA')

    def _clean_revision(self):
        """Normalise the revision input to 'R<digits>' (or '' if blank)."""
        import re as _re
        raw = (self.cleaned_data.get('lna_revision') or '').strip().upper()
        if not raw:
            return ''
        m = _re.match(r'^R?(\d+)$', raw)
        return f'R{m.group(1)}' if m else raw

    def clean(self):
        cleaned = super().clean()
        region = cleaned.get('region')
        name = cleaned.get('project_name')
        if self._is_lna(region):
            existing = (self.instance.proposal_reference
                        if self.instance and self.instance.pk else '') or ''
            parsed = parse_lna_reference(existing)
            # The revision input overrides whatever was embedded in the ref.
            revision = self._clean_revision()
            if parsed:
                # New or legacy LNA reference: keep the number, mirror the
                # current project name, apply the (editable) revision.
                number = parsed[0]
                cleaned['proposal_reference'] = build_lna_reference(number, name, revision)
            elif existing:
                # Unparseable non-LNA reference: leave it exactly as-is.
                cleaned['proposal_reference'] = existing
            else:
                # New LNA project: assign the next number.
                cleaned['proposal_reference'] = build_lna_reference(
                    next_lna_reference_number(), name, revision)
        elif not cleaned.get('proposal_reference'):
            self.add_error('proposal_reference', 'This field is required.')
        return cleaned

        # Filter owner choices based on user role
        if self.user:
            from accounts.models import User
            if self.user.is_super_admin_user:
                self.fields['owner'].queryset = User.objects.filter(is_active=True)
            elif self.user.is_admin_user or self.user.is_manager_user:
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
        ('NEO-Dubai', 'NEO Dubai'),
        ('NEO-KSA', 'NEO KSA'),
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
    owner = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate owner choices based on user role
        from accounts.models import User
        if user and user.is_super_admin_user:
            # Super admin sees all users
            users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        elif user and (user.is_admin_user or user.is_manager_user):
            # Manager sees users in their region
            users = User.objects.filter(is_active=True, region=user.region).order_by('first_name', 'last_name')
        else:
            # Sales rep doesn't need owner filter (sees only their own)
            users = User.objects.none()

        self.fields['owner'].choices = [('', 'All Owners')] + [
            (u.id, u.get_full_name() or u.username) for u in users
        ]


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
