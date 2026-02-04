from django import forms
from .models import SalesCallReport


class SalesCallReportForm(forms.ModelForm):
    """Form for creating and editing Sales Call Reports"""

    call_date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text="Date of the call/meeting"
    )

    next_action_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        help_text="Scheduled date for next action"
    )

    system_categories = forms.MultipleChoiceField(
        choices=SalesCallReport.SYSTEM_CATEGORY_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        required=True,
        label="Systems Relates",
        help_text="Select one or more system categories"
    )

    class Meta:
        model = SalesCallReport
        fields = [
            'call_date',
            'action_type',
            'contact_type',
            'system_categories',
            'company_name',
            'contact_name',
            'title',
            'role',
            'address',
            'phone',
            'email',
            'goal',
            'comments',
            'next_action_date',
            'next_action_type',
        ]
        widgets = {
            'action_type': forms.Select(attrs={'class': 'form-select'}),
            'contact_type': forms.Select(attrs={'class': 'form-select'}),
            'company_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter company name'}),
            'contact_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter contact name'}),
            'title': forms.Select(attrs={'class': 'form-select'}),
            'role': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., IT Manager, Procurement Head'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Company address'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone number'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'email@company.com'}),
            'goal': forms.Select(attrs={'class': 'form-select'}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Notes about the call/meeting...'}),
            'next_action_type': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If editing an existing instance, populate system_categories from stored value
        if self.instance and self.instance.pk:
            self.initial['system_categories'] = self.instance.get_system_categories_list()

    def clean_system_categories(self):
        """Convert list to comma-separated string for storage."""
        categories = self.cleaned_data.get('system_categories', [])
        return ','.join(categories)


class SalesCallReportFilterForm(forms.Form):
    """Filter form for Sales Call Reports list"""

    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search company or contact...'
        })
    )

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )

    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )

    action_type = forms.ChoiceField(
        required=False,
        choices=[('', 'All Action Types')] + list(SalesCallReport.ACTION_TYPE_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    contact_type = forms.ChoiceField(
        required=False,
        choices=[('', 'All Contact Types')] + list(SalesCallReport.CONTACT_TYPE_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    system_category = forms.ChoiceField(
        required=False,
        choices=[('', 'All Systems')] + list(SalesCallReport.SYSTEM_CATEGORY_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    goal = forms.ChoiceField(
        required=False,
        choices=[('', 'All Goals')] + list(SalesCallReport.GOAL_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    sales_rep = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, user=None, region_code=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate sales_rep choices based on user role
        from accounts.models import User
        if user and (user.is_admin_user or user.is_manager_user):
            reps = User.objects.select_related('region').order_by(
                'first_name', 'last_name'
            )
            if region_code:
                reps = reps.filter(region__code=region_code)
            self.fields['sales_rep'].choices = [('', 'All Sales Reps')] + [
                (rep.id, rep.get_full_name() or rep.username) for rep in reps
            ]
        else:
            # Hide sales_rep filter for regular sales reps
            del self.fields['sales_rep']
