from django import forms

from .models import CompanyDocument


class CompanyDocumentForm(forms.ModelForm):
    class Meta:
        model = CompanyDocument
        fields = ['title', 'document_type', 'custom_type', 'file',
                  'issuing_authority', 'reference_number',
                  'issue_date', 'expiry_date', 'notes']
        widgets = {
            'issue_date': forms.DateInput(attrs={'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
            'custom_type': forms.TextInput(
                attrs={'placeholder': 'Type a label (used when "Other")'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('custom_type', 'issuing_authority', 'reference_number',
                     'issue_date', 'expiry_date', 'notes'):
            self.fields[name].required = False
        # On edit, keep the existing file unless a new one is uploaded.
        if self.instance and self.instance.pk:
            self.fields['file'].required = False
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('document_type') == 'other' and not cleaned.get('custom_type'):
            self.add_error('custom_type', 'Enter a label for the "Other" document type.')
        return cleaned
