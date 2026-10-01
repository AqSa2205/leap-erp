from django import forms
from django.db import transaction
from django.forms import inlineformset_factory

from projects.models import Project

from .models import (
    ManpowerResource, ProjectIssue,
    GradeStructureLine, DesignationManpowerLine, EmploymentCostAssumptions,
    FirstYearMaintenanceLine, ManpowerCostingHeader,
)


def _bootstrapify(fields):
    for field in fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs['class'] = 'form-check-input'
        else:
            field.widget.attrs['class'] = 'form-control'


class ManpowerDetailsForm(forms.ModelForm):
    """Fill in / update the Project-Management-specific fields for an
    employee who already exists in HR. The employee themselves is fixed by
    the URL this form is reached from, not picked here."""

    class Meta:
        model = ManpowerResource
        fields = [
            'title', 'start_contract_date', 'end_contract_date',
            'engagement_type', 'discipline', 'employment_level', 'notes',
        ]
        widgets = {
            'start_contract_date': forms.DateInput(attrs={'type': 'date'}),
            'end_contract_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class NewEmployeeManpowerForm(forms.Form):
    """Onboarding someone who isn't in the HR system at all yet — creates
    both the hr.Employee record and this app's manpower details in one go.

    Deliberately a small subset of what hr.Employee can hold (full name, ID
    number, designation): the rest of HR's onboarding fields (medical
    insurance, blood group, org-chart manager, ...) belong to HR's own
    employee form, not to a quick add from Project Management."""

    full_name = forms.CharField(max_length=255, label='Resource Name')
    iqama_number = forms.CharField(max_length=50, label='ID Number (Iqama/Passport)')
    designation = forms.CharField(max_length=255, label='Title LEAP', required=False)

    title = forms.CharField(max_length=255, label='Title', required=False)
    start_contract_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    end_contract_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    engagement_type = forms.ChoiceField(
        choices=[('', '—')] + ManpowerResource.ENGAGEMENT_CHOICES, required=False)
    discipline = forms.ChoiceField(
        choices=[('', '—')] + ManpowerResource.DISCIPLINE_CHOICES, required=False)
    employment_level = forms.ChoiceField(
        choices=[('', '—')] + ManpowerResource.LEVEL_CHOICES, required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean_iqama_number(self):
        from hr.models import Employee

        value = self.cleaned_data['iqama_number'].strip()
        if Employee.objects.filter(iqama_number=value).exists():
            raise forms.ValidationError(
                'An employee with this ID number already exists in HR — '
                'find them in the list below and use Edit instead.')
        return value

    @transaction.atomic
    def save(self, created_by):
        from hr.models import Employee

        employee = Employee.objects.create(
            full_name=self.cleaned_data['full_name'],
            iqama_number=self.cleaned_data['iqama_number'],
            designation=self.cleaned_data['designation'],
            created_by=created_by,
        )
        return ManpowerResource.objects.create(
            employee=employee,
            created_by=created_by,
            title=self.cleaned_data['title'],
            start_contract_date=self.cleaned_data['start_contract_date'],
            end_contract_date=self.cleaned_data['end_contract_date'],
            engagement_type=self.cleaned_data['engagement_type'],
            discipline=self.cleaned_data['discipline'],
            employment_level=self.cleaned_data['employment_level'],
            notes=self.cleaned_data['notes'],
        )


class ProjectIssueForm(forms.ModelForm):
    class Meta:
        model = ProjectIssue
        fields = [
            'project', 'status', 'priority', 'description', 'owner',
            'date_identified', 'estimated_resolution_date', 'escalation_needed',
            'impact', 'actions', 'actual_resolution_date', 'final_resolution',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'impact': forms.Textarea(attrs={'rows': 2}),
            'actions': forms.Textarea(attrs={'rows': 2}),
            'final_resolution': forms.Textarea(attrs={'rows': 2}),
            'date_identified': forms.DateInput(attrs={'type': 'date'}),
            'estimated_resolution_date': forms.DateInput(attrs={'type': 'date'}),
            'actual_resolution_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
        # Keep the picker to projects this user could already reach through
        # the pipeline — same scoping ladder as everywhere else in pmo.
        if user is not None:
            from dashboard.views import projects_visible_to
            self.fields['project'].queryset = projects_visible_to(user).order_by('-id')


# ── Project Manpower Costing ─────────────────────────────────────────────────

class GradeStructureLineForm(forms.ModelForm):
    class Meta:
        model = GradeStructureLine
        fields = [
            'category', 'grade_code', 'discipline', 'typical_designation',
            'criteria', 'indicative_monthly_salary', 'order',
        ]
        widgets = {
            'criteria': forms.Textarea(attrs={'rows': 1}),
            'grade_code': forms.TextInput(attrs={'placeholder': 'EX-1'}),
            'discipline': forms.TextInput(attrs={
                'list': 'grade-discipline-suggestions', 'autocomplete': 'off'}),
            'indicative_monthly_salary': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control form-control-sm'

    def has_changed(self):
        """Same guard as PurchaseOrderItemForm: the formset's trailing blank
        row must not block a save just because it exists."""
        if not self.instance.pk:
            grade_code = (self.data.get(self.add_prefix('grade_code')) or '').strip()
            if not grade_code:
                return False
        return super().has_changed()


GradeStructureLineFormSet = inlineformset_factory(
    Project,
    GradeStructureLine,
    form=GradeStructureLineForm,
    extra=0,
    can_delete=True,
)


class DesignationManpowerLineForm(forms.ModelForm):
    class Meta:
        model = DesignationManpowerLine
        fields = [
            'designation', 'category', 'grade_code', 'discipline', 'nationality',
            'headcount', 'monthly_salary', 'order',
        ]
        widgets = {
            'grade_code': forms.TextInput(attrs={'placeholder': 'e.g. E-2'}),
            'discipline': forms.TextInput(attrs={
                'list': 'grade-discipline-suggestions', 'autocomplete': 'off'}),
            'headcount': forms.NumberInput(attrs={'min': '0'}),
            'monthly_salary': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control form-control-sm'

    def has_changed(self):
        if not self.instance.pk:
            designation = (self.data.get(self.add_prefix('designation')) or '').strip()
            if not designation:
                return False
        return super().has_changed()


DesignationManpowerLineFormSet = inlineformset_factory(
    Project,
    DesignationManpowerLine,
    form=DesignationManpowerLineForm,
    extra=0,
    can_delete=True,
)


class EmploymentCostAssumptionsForm(forms.ModelForm):
    class Meta:
        model = EmploymentCostAssumptions
        fields = [
            'overhead_pct', 'iqama_annual', 'medical_annual', 'ticket_annual',
            'esb_pct', 'gosi_expat_pct', 'gosi_saudi_pct',
        ]
        widgets = {
            'overhead_pct': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'iqama_annual': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'medical_annual': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'ticket_annual': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'esb_pct': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'gosi_expat_pct': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'gosi_saudi_pct': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control form-control-sm'


class FirstYearMaintenanceLineForm(forms.ModelForm):
    class Meta:
        model = FirstYearMaintenanceLine
        fields = [
            'section', 'description', 'nature_of_expense', 'amount', 'qty',
            'remarks', 'order',
        ]
        widgets = {
            'amount': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'qty': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'remarks': forms.TextInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control form-control-sm'

    def has_changed(self):
        if not self.instance.pk:
            description = (self.data.get(self.add_prefix('description')) or '').strip()
            if not description:
                return False
        return super().has_changed()


FirstYearMaintenanceLineFormSet = inlineformset_factory(
    Project,
    FirstYearMaintenanceLine,
    form=FirstYearMaintenanceLineForm,
    extra=0,
    can_delete=True,
)


class ManpowerCostingHeaderForm(forms.ModelForm):
    class Meta:
        model = ManpowerCostingHeader
        fields = ['po_type', 'po_value', 'currency_override', 'vat_rate', 'profit_margin_pct']
        widgets = {
            'po_value': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'currency_override': forms.TextInput(attrs={'placeholder': 'Leave blank to use the project region currency'}),
            'vat_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'profit_margin_pct': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '99'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class ProjectPOBasicsForm(forms.ModelForm):
    """PO Number / PO Date live on Project (shared with the rest of the
    ERP), but a PM pricing a bid often doesn't have them yet — this lets
    them be filled in here, later, rather than only from the pipeline."""

    class Meta:
        model = Project
        fields = ['po_number', 'estimated_po_date']
        widgets = {
            'estimated_po_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control form-control-sm'
