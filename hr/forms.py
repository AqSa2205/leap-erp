from django import forms
from .models import Employee, Asset, AssetAssignment, Vehicle, EmployeeDocument, VehicleDocument, LeaveType, Holiday, LeaveRecord, AttendanceSettings, WorkingDay, WFHRecord


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            'iqama_number', 'iqama_issued_on', 'iqama_expires_on',
            'medical_insurance_issued_on', 'medical_insurance_expires_on',
            'full_name', 'designation', 'qualification',
            'date_of_birth', 'joining_date', 'nationality', 'marital_status',
            'blood_group', 'personal_email', 'documents_link', 'deployment',
            'contract_type', 'work_email', 'mobile_number',
            'is_active', 'inactive_from', 'user',
        ]
        widgets = {
            'user': forms.Select(attrs={'class': 'form-select'}),
            'inactive_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'iqama_number': forms.TextInput(attrs={'class': 'form-control'}),
            'iqama_issued_on': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'iqama_expires_on': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'medical_insurance_issued_on': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'medical_insurance_expires_on': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone
        from accounts.models import User
        self.fields['user'].required = False
        self.fields['user'].label = 'Linked Login Account'
        self.fields['user'].help_text = (
            'The account that logs in as this employee (for the self-service '
            'portal). Leave blank if none.')
        self.fields['user'].queryset = User.objects.filter(
            is_active=True).order_by('username')
        # Inactive-from: optional, and the date picker can't go past today.
        self.fields['inactive_from'].required = False
        self.fields['inactive_from'].label = 'Inactive From'
        self.fields['inactive_from'].widget.attrs['max'] = (
            timezone.localdate().isoformat())

    def clean(self):
        from django.utils import timezone
        cleaned = super().clean()
        for issue, expiry, label in (
            ('iqama_issued_on', 'iqama_expires_on', 'Iqama'),
            ('medical_insurance_issued_on', 'medical_insurance_expires_on', 'Medical insurance'),
        ):
            issued = cleaned.get(issue)
            expires = cleaned.get(expiry)
            if issued and expires and expires < issued:
                self.add_error(expiry, f'{label} expiry date cannot be before its issue date.')

        # Inactive-from is only meaningful when the employee is inactive.
        today = timezone.localdate()
        inactive_from = cleaned.get('inactive_from')
        if cleaned.get('is_active'):
            cleaned['inactive_from'] = None          # active -> no inactive date
        else:
            if not inactive_from:
                cleaned['inactive_from'] = today      # default to today
            elif inactive_from > today:
                self.add_error('inactive_from',
                               'Inactive-from date cannot be in the future.')
        return cleaned


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
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Statuses'), ('active', 'Active'), ('inactive', 'Inactive')],
        widget=forms.Select(attrs={'class': 'form-select'}),
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


class VehicleDocumentForm(forms.ModelForm):
    class Meta:
        model = VehicleDocument
        fields = ['document_type', 'custom_type', 'title', 'file', 'expiry_date', 'notes']
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
            'custom_type': forms.TextInput(attrs={'placeholder': 'Type a label (used when "Other")'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['custom_type'].required = False
        self.fields['expiry_date'].required = False
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


class AssetIssueForm(forms.ModelForm):
    """Issue an asset to an employee — creates a new active AssetAssignment."""

    class Meta:
        model = AssetAssignment
        fields = ['employee', 'assigned_at', 'condition_out', 'handover_form', 'issue_notes']
        widgets = {
            'assigned_at': forms.DateInput(attrs={'type': 'date'}),
            'issue_notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional: serial-cross-check, accessories, etc.'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].queryset = Employee.objects.filter(is_active=True).order_by('full_name')
        self.fields['employee'].empty_label = 'Select employee...'
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs['class'] = 'form-control'


class AssetReturnForm(forms.ModelForm):
    """Close an active AssetAssignment by recording the return."""

    class Meta:
        model = AssetAssignment
        fields = ['returned_at', 'condition_in', 'return_notes']
        widgets = {
            'returned_at': forms.DateInput(attrs={'type': 'date'}),
            'return_notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional: damage description, missing accessories, etc.'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['returned_at'].required = True
        self.fields['condition_in'].required = True
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'


# ─── Leave Type & Holiday Forms ──────────────────────────────


class LeaveTypeForm(forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ['name', 'code', 'default_annual_days', 'is_paid', 'color',
                  'requires_medical_certificate', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'default_annual_days': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'color': forms.TextInput(attrs={'class': 'form-control'}),
            'requires_medical_certificate': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['date', 'name', 'is_active']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class WorkingDayForm(forms.ModelForm):
    class Meta:
        model = WorkingDay
        fields = ['date', 'name', 'is_active']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class LeaveRecordForm(forms.ModelForm):
    class Meta:
        model = LeaveRecord
        fields = ['employee', 'leave_type', 'start_date', 'end_date', 'days', 'note', 'medical_certificate']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'leave_type': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'days': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'placeholder': 'Auto if blank'}),
            'note': forms.TextInput(attrs={'class': 'form-control'}),
            'medical_certificate': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }


class WFHRecordForm(forms.ModelForm):
    class Meta:
        model = WFHRecord
        fields = ['employee', 'start_date', 'end_date', 'note']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'note': forms.TextInput(attrs={'class': 'form-control'}),
        }


WEEKDAY_CHOICES = [(0, 'Mon'), (1, 'Tue'), (2, 'Wed'), (3, 'Thu'), (4, 'Fri'), (5, 'Sat'), (6, 'Sun')]


class AttendanceSettingsForm(forms.Form):
    weekend_days = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES, required=False,
        widget=forms.CheckboxSelectMultiple)
    expected_in_by = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}, format='%H:%M'),
        input_formats=['%H:%M'])

    def initial_from(self, settings_obj):
        self.initial['weekend_days'] = [str(x) for x in sorted(settings_obj.weekend_day_set())]
        self.initial['expected_in_by'] = settings_obj.expected_in_by
