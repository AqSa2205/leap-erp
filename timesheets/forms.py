from datetime import date

from django import forms

from .models import ActivityCode, TimesheetEntry


class TimesheetEntryForm(forms.ModelForm):
    """Employee-facing form to log one day's work. The `employee` field is
    never exposed here — it's set server-side from request.user in the view,
    exactly like LeaveRequestForm's fixed_employee pattern. This is the fix
    for the 'never trust employee/status from POST' rule."""

    class Meta:
        model = TimesheetEntry
        fields = ['date', 'activity_code', 'task_description', 'hours']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'activity_code': forms.Select(attrs={'class': 'form-select'}),
            'task_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'min': '0', 'max': '24'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active codes — a code someone deactivated shouldn't be
        # pickable for new entries, even though old entries keep referencing it.
        self.fields['activity_code'].queryset = ActivityCode.objects.filter(is_active=True)
        # Defaults for a brand-new entry only -- editing an existing entry
        # (self.instance.pk is set) must keep showing that entry's real
        # date/hours, not silently overwrite them with these defaults.
        if not self.instance.pk:
            self.fields['date'].initial = date.today()
            self.fields['hours'].initial = 10

    def clean_hours(self):
        hours = self.cleaned_data['hours']
        if hours <= 0:
            raise forms.ValidationError('Hours must be greater than 0.')
        if hours > 24:
            raise forms.ValidationError('Hours cannot exceed 24 in a single day.')
        return hours