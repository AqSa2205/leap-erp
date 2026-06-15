from django import forms
from accounts.models import AI_DEVELOPER_ROLE_NAMES, User
from .models import DevTask


class DevTaskForm(forms.ModelForm):
    class Meta:
        model = DevTask
        fields = ['developer', 'title', 'description', 'priority',
                  'estimated_hours', 'due_date', 'github_url']
        widgets = {
            'developer': forms.Select(attrs={'class': 'form-select'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'estimated_hours': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'github_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'Optional PR/branch link'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['developer'].queryset = User.objects.filter(
            role__name__in=AI_DEVELOPER_ROLE_NAMES, is_active=True).order_by('username')
