from django import forms
from accounts.models import AI_DEVELOPER_ROLE_NAMES, User
from .models import DevTask, TaskStack


class DevTaskForm(forms.ModelForm):
    class Meta:
        model = DevTask
        fields = ['developer', 'title', 'description', 'priority',
                  'estimated_hours', 'due_date', 'github_url', 'stack']
        widgets = {
            'developer': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'estimated_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'github_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'Optional PR/branch link'}),
            'stack': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['developer'].queryset = User.objects.filter(
            role__name__in=AI_DEVELOPER_ROLE_NAMES, is_active=True).order_by('username')
        self.fields['stack'].required = False
        self.fields['stack'].queryset = TaskStack.objects.all()

    def clean_title(self):
        title = self.cleaned_data.get('title', '')
        qs = DevTask.objects.filter(title__iexact=title)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('A task with this title already exists.')
        return title


class BulkTaskForm(forms.Form):
    """Create a whole list of backlog tasks at once — one title per line."""
    titles = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 10,
                                     'placeholder': 'One task per line, e.g.\nBuild login API\nWrite unit tests\nSet up CI'}),
        help_text='Each non-empty line becomes an unassigned task you can assign later.')
    priority = forms.ChoiceField(choices=DevTask.PRIORITY_CHOICES, initial='medium',
                                 widget=forms.Select(attrs={'class': 'form-select'}))
    due_date = forms.DateField(required=False,
                               widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
                               help_text='Optional — applied to every task in this list (editable per task on assignment).')

    def clean_titles(self):
        lines = [ln.strip() for ln in self.cleaned_data['titles'].splitlines()]
        titles = [ln for ln in lines if ln]
        if not titles:
            raise forms.ValidationError('Enter at least one task.')
        return titles


class StackForm(forms.Form):
    name = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    titles = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 8,
                             'placeholder': 'Optional: one task per line to seed this stack'}),
                             help_text='Optional — each line becomes an unassigned task inside this stack.')
    priority = forms.ChoiceField(choices=DevTask.PRIORITY_CHOICES, initial='medium',
                                 widget=forms.Select(attrs={'class': 'form-select'}))

    def clean_titles(self):
        return [ln.strip() for ln in (self.cleaned_data.get('titles') or '').splitlines() if ln.strip()]


class AssignTaskForm(forms.ModelForm):
    """Assign a single backlog task to a developer (set who + the details)."""
    class Meta:
        model = DevTask
        fields = ['developer', 'priority', 'estimated_hours', 'due_date', 'github_url']
        widgets = {
            'developer': forms.Select(attrs={'class': 'form-select'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'estimated_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'github_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'Optional PR/branch link'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['developer'].required = True
        self.fields['developer'].queryset = User.objects.filter(
            role__name__in=AI_DEVELOPER_ROLE_NAMES, is_active=True).order_by('username')
