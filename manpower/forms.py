from django import forms
from .models import ManpowerSheet, ManpowerLineItem


class SheetForm(forms.ModelForm):
    class Meta:
        model = ManpowerSheet
        fields = ['title', 'project_reference', 'date', 'notes']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'project_reference': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. PRJ-2024-001',
            }),
            'date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
            }),
        }


EMPLOYEE_FIELDS = [
    'employee_name', 'department', 'classification', 'location_project',
    'designation', 'doj', 'demobilization_date', 'date_of_birth',
    'budget_cost', 'gross_salary', 'iqama_cost', 'service_transfer_visa_fee',
    'gosi_cost', 'vacation_pay', 'exe_cost', 'eosb',
    'air_ticket', 'insurance_cost', 'project_allowance', 'ppe',
    'engineering_council_cost', 'other_expenditures', 'order',
]

EMPLOYEE_WIDGETS = {
    'employee_name': forms.TextInput(attrs={'class': 'form-control'}),
    'department': forms.TextInput(attrs={'class': 'form-control'}),
    'classification': forms.TextInput(attrs={'class': 'form-control'}),
    'location_project': forms.TextInput(attrs={'class': 'form-control'}),
    'designation': forms.TextInput(attrs={'class': 'form-control'}),
    'doj': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    'demobilization_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    'budget_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'gross_salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'iqama_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'service_transfer_visa_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'gosi_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'vacation_pay': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'exe_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'eosb': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'air_ticket': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'insurance_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'project_allowance': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'ppe': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'engineering_council_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'other_expenditures': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
    'order': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
}


class LineItemForm(forms.ModelForm):
    class Meta:
        model = ManpowerLineItem
        fields = EMPLOYEE_FIELDS
        widgets = EMPLOYEE_WIDGETS


class EmployeeCreateForm(forms.ModelForm):
    """Form for creating an employee from the main list page with a sheet selector."""
    sheet = forms.ModelChoiceField(
        queryset=ManpowerSheet.objects.all().order_by('-date', 'title'),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Manpower Sheet',
    )

    class Meta:
        model = ManpowerLineItem
        fields = ['sheet'] + EMPLOYEE_FIELDS
        widgets = EMPLOYEE_WIDGETS


class FilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search name, department, designation, project...',
        }),
    )


class ImportForm(forms.Form):
    excel_file = forms.FileField(
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls',
        }),
    )
