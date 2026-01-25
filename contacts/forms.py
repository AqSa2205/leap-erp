from django import forms
from .models import ContactDatabase


class ContactDatabaseForm(forms.ModelForm):
    """Form for creating and editing contacts in the database."""

    class Meta:
        model = ContactDatabase
        fields = [
            'category', 'notice_identifier', 'notice_type', 'serial_number',
            'status', 'published_date', 'organisation_name', 'title',
            'description', 'nationwide', 'postcode', 'region', 'contact_name',
            'contact_email', 'contact_address', 'contact_telephone',
            'contact_website', 'cpv_codes', 'last_contact', 'comments'
        ]
        widgets = {
            'category': forms.Select(attrs={'class': 'form-select'}),
            'notice_identifier': forms.TextInput(attrs={'class': 'form-control'}),
            'notice_type': forms.Select(attrs={'class': 'form-select'}),
            'serial_number': forms.NumberInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'published_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'organisation_name': forms.TextInput(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'nationwide': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'postcode': forms.TextInput(attrs={'class': 'form-control'}),
            'region': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_name': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'contact_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'contact_telephone': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_website': forms.URLInput(attrs={'class': 'form-control'}),
            'cpv_codes': forms.TextInput(attrs={'class': 'form-control'}),
            'last_contact': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'comments': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class ContactDatabaseFilterForm(forms.Form):
    """Form for filtering contacts in the database."""

    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search organisation, contact...'
        })
    )
    category = forms.ChoiceField(
        required=False,
        choices=[('', 'All Categories')] + list(ContactDatabase.CATEGORY_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Status')] + list(ContactDatabase.STATUS_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    region = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Region...'
        })
    )


class ContactImportForm(forms.Form):
    """Form for importing contacts from Excel."""

    excel_file = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'})
    )
    category = forms.ChoiceField(
        choices=ContactDatabase.CATEGORY_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
