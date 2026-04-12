from django import forms
from .models import Employee, Asset, Vehicle, EmployeeDocument


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            'iqama_number', 'full_name', 'designation', 'qualification',
            'date_of_birth', 'joining_date', 'nationality', 'marital_status',
            'blood_group', 'personal_email', 'documents_link', 'deployment',
            'contract_type', 'work_email', 'mobile_number', 'is_active',
        ]
        widgets = {
            'iqama_number': forms.TextInput(attrs={'class': 'form-control'}),
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'designation': forms.TextInput(attrs={'class': 'form-control'}),
            'qualification': forms.TextInput(attrs={'class': 'form-control'}),
            'date_of_birth': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'joining_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'nationality': forms.TextInput(attrs={'class': 'form-control'}),
            'marital_status': forms.Select(attrs={'class': 'form-select'}),
            'blood_group': forms.Select(attrs={'class': 'form-select'}),
            'personal_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'documents_link': forms.URLInput(attrs={'class': 'form-control'}),
            'deployment': forms.TextInput(attrs={'class': 'form-control'}),
            'contract_type': forms.Select(attrs={'class': 'form-select'}),
            'work_email': forms.EmailInput(attrs={'class': 'form-control'}),
            'mobile_number': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class EmployeeFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search name, iqama, email...',
        }),
    )
    contract_type = forms.ChoiceField(
        required=False,
        choices=[('', 'All Contracts')] + list(Employee.CONTRACT_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    nationality = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Nationality...',
        }),
    )
    deployment = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Deployment...',
        }),
    )


class EmployeeImportForm(forms.Form):
    excel_file = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'}),
    )


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            'asset_name', 'asset_type', 'serial_number', 'specifications',
            'invoice_number', 'employee_name', 'department', 'designation',
            'handover_date', 'handover_by', 'condition', 'return_date',
            'return_to', 'quantity', 'purchase_date', 'price',
            'planned_life', 'in_stock',
        ]
        widgets = {
            'asset_name': forms.TextInput(attrs={'class': 'form-control'}),
            'asset_type': forms.TextInput(attrs={'class': 'form-control', 'list': 'asset-type-list', 'placeholder': 'e.g. Laptop, Monitor'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'specifications': forms.TextInput(attrs={'class': 'form-control'}),
            'invoice_number': forms.TextInput(attrs={'class': 'form-control'}),
            'employee_name': forms.TextInput(attrs={'class': 'form-control'}),
            'department': forms.TextInput(attrs={'class': 'form-control'}),
            'designation': forms.TextInput(attrs={'class': 'form-control'}),
            'handover_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'handover_by': forms.TextInput(attrs={'class': 'form-control'}),
            'condition': forms.Select(attrs={'class': 'form-select'}),
            'return_date': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Date or notes'}),
            'return_to': forms.TextInput(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'purchase_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'planned_life': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 3 Years'}),
            'in_stock': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AssetFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search name, serial, employee...',
        }),
    )
    asset_type = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Asset type...',
        }),
    )
    condition = forms.ChoiceField(
        required=False,
        choices=[('', 'All Conditions')] + list(Asset.CONDITION_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    in_stock = forms.ChoiceField(
        required=False,
        choices=[('', 'All'), ('true', 'In Stock'), ('false', 'Assigned')],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class AssetImportForm(forms.Form):
    excel_file = forms.FileField(
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx,.xls'}),
    )


# ─── Vehicle Forms ────────────────────────────────────────────

class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            'plate_number', 'plate_type', 'sequence_number', 'chassis_number',
            'vehicle_maker', 'vehicle_model', 'model_year', 'major_color', 'body_type',
            'vehicle_status', 'mvpi_status', 'insurance_status', 'restriction_status',
            'ownership_date', 'license_expiry', 'license_issue_date', 'inspection_expiry',
            'driver_id', 'driver_name', 'branch_name',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'


class VehicleFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Search plate, maker, driver...'}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Statuses')] + list(Vehicle.VEHICLE_STATUS_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


# ─── Employee Document Form ──────────────────────────────────

class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ['document_type', 'title', 'file', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs['class'] = 'form-control'
