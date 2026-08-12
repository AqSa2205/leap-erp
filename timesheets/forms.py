from datetime import date

from django import forms

from .models import ActivityCode, TimesheetEntry
from projects.models import Project


class TimesheetEntryForm(forms.ModelForm):
    """Employee-facing form to log one day's work. The `employee` field is
    never exposed here — it's set server-side from request.user in the view,
    exactly like LeaveRequestForm's fixed_employee pattern.

    entry_type is a UI-only toggle (not a model field): it decides whether
    the employee is logging client-project work or internal activity work,
    and drives which of `project` / `activity_code` actually gets saved.
    The model's own clean() still enforces 'exactly one' as the source of
    truth — this form just makes sure the *other* field is always blanked
    out before that check runs, so the toggle can't leave stale data behind.
    """

    ENTRY_TYPE_PROJECT = 'project'
    ENTRY_TYPE_ACTIVITY = 'activity'
    ENTRY_TYPE_CHOICES = [
        (ENTRY_TYPE_ACTIVITY, 'Internal Activity'),
        (ENTRY_TYPE_PROJECT, 'Client Project'),
    ]

    entry_type = forms.ChoiceField(
        choices=ENTRY_TYPE_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'entry-type-toggle'}),
        initial=ENTRY_TYPE_ACTIVITY,
    )

    class Meta:
        model = TimesheetEntry
        fields = ['entry_type', 'date', 'project', 'activity_code', 'task_description', 'hours']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'activity_code': forms.Select(attrs={'class': 'form-select'}),
            'project': forms.Select(attrs={'class': 'form-select'}),
            'task_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'min': '0', 'max': '24'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Only active codes are pickable for new entries; old entries keep
        # referencing whatever code they already had, even if deactivated.
        self.fields['activity_code'].queryset = ActivityCode.objects.filter(is_active=True)

        # Only "won" and "ongoing" projects are pickable — matches how HR
        # actually wants employees logging time against real, active client
        # work, not proposals still being chased or work that's closed out.
        self.fields['project'].queryset = Project.objects.filter(
            status__category__in=['won', 'ongoing']
        )

        # Neither is required at the form-field level — clean() below
        # enforces exactly one is filled, based on the toggle.
        self.fields['project'].required = False
        self.fields['activity_code'].required = False

        if not self.instance.pk:
            # Defaults for a brand-new entry only — editing an existing
            # entry must keep showing that entry's real date/hours.
            self.fields['date'].initial = date.today()
            self.fields['hours'].initial = 10
        else:
            # Editing: set the toggle to match what this entry actually has,
            # so the correct dropdown shows pre-selected instead of
            # defaulting back to "Internal Activity".
            self.fields['entry_type'].initial = (
                self.ENTRY_TYPE_PROJECT if self.instance.project_id
                else self.ENTRY_TYPE_ACTIVITY
            )

    def clean(self):
        cleaned_data = super().clean()
        entry_type = cleaned_data.get('entry_type')
        project = cleaned_data.get('project')
        activity_code = cleaned_data.get('activity_code')

        if entry_type == self.ENTRY_TYPE_PROJECT:
            if not project:
                self.add_error('project', 'Select a project.')
            cleaned_data['activity_code'] = None
        elif entry_type == self.ENTRY_TYPE_ACTIVITY:
            if not activity_code:
                self.add_error('activity_code', 'Select an activity code.')
            cleaned_data['project'] = None

        return cleaned_data

    def clean_hours(self):
        hours = self.cleaned_data['hours']
        if hours <= 0:
            raise forms.ValidationError('Hours must be greater than 0.')
        if hours > 24:
            raise forms.ValidationError('Hours cannot exceed 24 in a single day.')
        return hours