from django import forms
from .models import ExchangeRate, CostingSheet, CostingSection, CostingLineItem
from projects.models import Project


class CostingSheetForm(forms.ModelForm):
    class Meta:
        model = CostingSheet
        fields = [
            'title', 'project', 'customer_reference',
            'margin', 'discount_rate', 'shipping_rate', 'customs_rate',
            'finances_rate', 'installation_rate',
            'output_currency', 'status',
        ]
        widgets = {
            'margin': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '1'}),
            'discount_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '1'}),
            'shipping_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '1'}),
            'customs_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '1'}),
            'finances_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '1'}),
            'installation_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '1'}),
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

        if self.user:
            if self.user.is_admin_user:
                pass
            elif self.user.is_manager_user:
                self.fields['project'].queryset = Project.objects.filter(
                    region=self.user.region
                )
            else:
                self.fields['project'].queryset = Project.objects.filter(
                    owner=self.user
                )


class CostingSectionForm(forms.ModelForm):
    class Meta:
        model = CostingSection
        fields = ['section_number', 'title', 'order']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
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
