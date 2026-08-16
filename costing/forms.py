from django import forms
from .models import ExchangeRate, CostingSheet, CostingSection, CostingLineItem, TermsTemplate, ScopeOfWorkItem, ClientRemarkTemplate
from projects.models import Project


class CostingSheetForm(forms.ModelForm):
    class Meta:
        model = CostingSheet
        fields = [
            'title', 'project', 'customer_reference',
            'margin', 'discount_rate', 'shipping_rate', 'customs_rate',
            'finances_rate', 'installation_rate',
            'output_currency', 'status',
            'customer_name', 'end_user', 'contact_person', 'telephone', 'fax',
        ]
        widgets = {
            'margin': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '99', 'placeholder': 'e.g. 40 for 40% (max 99)'}),
            'discount_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g. 5 for 5%'}),
            'shipping_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g. 3 for 3%'}),
            'customs_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g. 5 for 5%'}),
            'finances_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g. 2 for 2%'}),
            'installation_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g. 10 for 10%'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['status'].widget.attrs['class'] = 'form-select'
        self.fields['project'].required = False
        self.fields['project'].queryset = Project.objects.select_related('region').all()

        if self.user and not self.user.is_super_admin_user:
            self.fields['project'].queryset = Project.objects.filter(
                region=self.user.region
            ).select_related('region')



class CostingSectionForm(forms.ModelForm):
    class Meta:
        model = CostingSection
        fields = ['section_number', 'title', 'order', 'is_optional']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == 'is_optional':
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'


class CostingLineItemForm(forms.ModelForm):
    class Meta:
        model = CostingLineItem
        fields = [
            'item_number', 'description', 'make', 'model_number',
            'quantity', 'unit', 'vendor_name', 'system',
            'supplier_currency', 'base_unit_cost', 'discount_pct',
            'shipping_pct', 'customs_pct', 'finances_pct', 'installation_pct',
            'margin', 'order',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'


class ExchangeRateForm(forms.ModelForm):
    class Meta:
        model = ExchangeRate
        fields = ['currency_code', 'currency_name', 'rate_to_usd']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class TermsTemplateForm(forms.ModelForm):
    class Meta:
        model = TermsTemplate
        fields = ['name', 'category', 'usage', 'content']
        widgets = {
            'content': forms.Textarea(attrs={'rows': 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'

        self.fields['content'].widget.attrs['class'] += ' tinymce-editor'

class ClientRemarkTemplateForm(forms.ModelForm):
    class Meta:
        model = ClientRemarkTemplate
        fields = ['name']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].widget.attrs['class'] = 'form-control'


class CostingFilterForm(forms.Form):
    search = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'placeholder': 'Search costing sheets...',
    }))
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Statuses')] + CostingSheet.STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
