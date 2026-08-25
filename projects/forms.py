from django import forms
from .models import (
    Project, Region, ProjectStatus, Document,
    build_lna_reference, next_lna_reference_number,
    parse_lna_reference, lna_reference_kind,
    split_trailing_revision, join_trailing_revision,
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

    # An email picked from the live inbox before the project exists yet (see
    # projects/views.py: link_pipeline_email_new). Not a model field — just
    # opaque JSON riding along in the form so the existing draft-autosave
    # system (drafts.FormDraft) picks it up and restores it automatically,
    # same as every other field. Materialized into a real PipelineEmail +
    # Documents only once the project is actually created.
    picked_email_json = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = Project
        fields = [
            'project_name', 'proposal_reference', 'client_rfq_reference',
            'po_number', 'submission_deadline', 'estimated_po_date',
            'bom_started_deadline', 'handed_over_deadline', 'costing_started_deadline', 'finalized_deadline',
            'owner', 'customer', 'end_user', 'project_stage', 'priority', 'status', 'region', 'year', 'estimated_value',
            'estimated_value_usd', 'estimated_value_per_annum', 'estimated_gp',
            'actual_sales', 'po_award_quarter', 'success_quotient', 'minimum_achievement',
            'contact_with', 'remarks', 'notes', 'portal_url',
            'lost_reason', 'lost_comment',
        ]
        widgets = {
            'lost_comment': forms.Textarea(attrs={'rows': 2}),
            'submission_deadline': forms.DateInput(attrs={'type': 'date'}),
            'estimated_po_date': forms.DateInput(attrs={'type': 'date'}),
            'bom_started_deadline': forms.DateInput(attrs={'type': 'date'}),
            'handed_over_deadline': forms.DateInput(attrs={'type': 'date'}),
            'costing_started_deadline': forms.DateInput(attrs={'type': 'date'}),
            'finalized_deadline': forms.DateInput(attrs={'type': 'date'}),
            'remarks': forms.Textarea(attrs={'rows': 3}),
            'notes': forms.Textarea(attrs={'rows': 3}),
            'year': forms.Select(choices=[('', 'Select Year')] + Project.YEAR_CHOICES),
            'priority': forms.Select(choices=[('', 'Select Priority')] + Project.PRIORITY_CHOICES),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Which status options mean "lost", so the template can reveal the
        # reason fields without hardcoding ids or matching on a label that
        # someone may rename.
        self.lost_status_ids = list(
            ProjectStatus.objects.filter(category='lost').values_list('id', flat=True))

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

    def _validate_lost_reason(self, cleaned):
        """A project marked Lost has to say why.

        The reason is only demanded when the status is actually Lost, and an
        existing reason is never cleared when the status moves away — projects
        do get revived, and wiping the record of what went wrong the first time
        loses the very thing this field exists to capture.
        """
        status = cleaned.get('status')
        if not status or status.category != 'lost':
            return
        reason = cleaned.get('lost_reason')
        if not reason:
            self.add_error('lost_reason',
                           'Choose why this opportunity was lost.')
            return
        if reason == Project.LOST_OTHER and not (cleaned.get('lost_comment') or '').strip():
            # "Other" with no comment records nothing useful — it is the one
            # answer that cannot be interpreted later without the context.
            self.add_error('lost_comment',
                           'Add a comment explaining the loss when the reason is Other.')

    def clean(self):
        cleaned = super().clean()
        self._validate_lost_reason(cleaned)
        region = cleaned.get('region')
        name = cleaned.get('project_name')
        if self._is_lna(region):
            existing = (self.instance.proposal_reference
                        if self.instance and self.instance.pk else '') or ''
            kind = lna_reference_kind(existing)
            # The revision input overrides whatever was embedded in the ref.
            revision = self._clean_revision()
            if kind in ('canonical', 'code'):
                # Rebuild from number + current name + revision.
                number = parse_lna_reference(existing)[0]
                cleaned['proposal_reference'] = build_lna_reference(number, name, revision)
            elif kind == 'named':
                # Name embedded in a non-canonical format: only swap the trailing
                # revision in place, preserving the base and its style.
                base, _old, style = split_trailing_revision(existing)
                cleaned['proposal_reference'] = join_trailing_revision(
                    base, revision, style or 'dash')
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
    # Commercial-pipeline workflow stage. Choices are populated in __init__
    # from CostingSheet.WORKFLOW_STAGE_CHOICES (imported there to avoid a
    # module-load import cycle with costing.models). The extra 'none' option
    # matches projects with no costing sheet started yet.
    workflow_stage = forms.ChoiceField(
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
        # Populate workflow-stage choices from the costing app.
        from costing.models import CostingSheet
        self.fields['workflow_stage'].choices = (
            [('', 'All Workflow Stages'), ('none', 'Not started (no costing)')]
            + CostingSheet.WORKFLOW_STAGE_CHOICES
        )
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
