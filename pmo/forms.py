from django import forms
from django.db import transaction

from .models import ManpowerResource, ProjectIssue


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
