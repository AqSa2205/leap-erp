from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from .models import Employee, Asset, AssetAssignment, Vehicle, EmployeeDocument, VehicleDocument, LeaveType, Holiday, AttendanceSettings, WorkingDay, WFHRecord, AttendanceException






import re
from django.core.exceptions import ValidationError

NAME_RE = re.compile(
    r"^[A-Za-z\u00C0-\u017F"      # Latin + accented Latin
    r"\u0600-\u06FF"                # Arabic
    r"\u0750-\u077F"                # Arabic Supplement
    r"\u08A0-\u08FF"                # Arabic Extended-A
    r"\uFB50-\uFDFF"                # Arabic Presentation Forms-A
    r"\uFE70-\uFEFF"                # Arabic Presentation Forms-B
    r"\s'\-]+$"
)
TITLE_RE = re.compile(
    r"^[A-Za-z0-9"
    r"\u0600-\u06FF"
    r"\u0750-\u077F"
    r"\u08A0-\u08FF"
    r"\uFB50-\uFDFF"
    r"\uFE70-\uFEFF"
    r"\s.,&/\-]+$"
)
CODE_RE = re.compile(r"^[A-Za-z0-9\-/]+$")
PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,20}$")


def validate_name_field(value, field_label):
    value = (value or '').strip()
    if value and not NAME_RE.match(value):
        raise ValidationError(f'{field_label} should only contain letters, spaces, hyphens, and apostrophes.')
    return value


def validate_title_field(value, field_label):
    value = (value or '').strip()
    if value and not TITLE_RE.match(value):
        raise ValidationError(f'{field_label} contains invalid characters.')
    return value


def validate_code_field(value, field_label):
    value = (value or '').strip()
    if value and not CODE_RE.match(value):
        raise ValidationError(f'{field_label} should only contain letters, numbers, and hyphens.')
    return value


def validate_phone_field(value, field_label='Phone number'):
    value = (value or '').strip()
    if value and not PHONE_RE.match(value):
        raise ValidationError(f'{field_label} is not a valid phone number.')
    return value

class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            'iqama_number', 'iqama_issued_on', 'iqama_expires_on',
            'medical_insurance_issued_on', 'medical_insurance_expires_on',
            'full_name', 'designation', 'qualification',
            'date_of_birth', 'joining_date', 'nationality', 'marital_status',
            'blood_group', 'personal_email', 'documents_link', 'deployment', 'grade', 'picture',
            'work_location', 'contract_type', 'work_email', 'mobile_number',
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
            'grade': forms.TextInput(attrs={'class': 'form-control'}),
            'picture': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'work_location': forms.Select(attrs={'class': 'form-select'}),
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
        self.fields['inactive_from'].required = False
        self.fields['inactive_from'].label = 'Inactive From'
        self.fields['inactive_from'].widget.attrs['max'] = (
            timezone.localdate().isoformat())

    def clean_full_name(self):
        return validate_name_field(self.cleaned_data.get('full_name'), 'Full name')

    def clean_nationality(self):
        return validate_name_field(self.cleaned_data.get('nationality'), 'Nationality')

    def clean_designation(self):
        return validate_title_field(self.cleaned_data.get('designation'), 'Designation')

    def clean_qualification(self):
        return validate_title_field(self.cleaned_data.get('qualification'), 'Qualification')

    def clean_deployment(self):
        return validate_title_field(self.cleaned_data.get('deployment'), 'Deployment')

    def clean_grade(self):
        return validate_title_field(self.cleaned_data.get('grade'), 'Grade')

    def clean_picture(self):
        # A very large photo would upload fine but then get embedded into
        # every subsequent Excel export, making that file huge too.
        picture = self.cleaned_data.get('picture')
        max_size = 5 * 1024 * 1024  # 5 MB
        if picture and hasattr(picture, 'size') and picture.size > max_size:
            raise ValidationError('Picture must be smaller than 5 MB.')
        return picture

    def clean_iqama_number(self):
        return validate_code_field(self.cleaned_data.get('iqama_number'), 'Iqama number')

    def clean_mobile_number(self):
        return validate_phone_field(self.cleaned_data.get('mobile_number'), 'Mobile number')

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

        today = timezone.localdate()
        inactive_from = cleaned.get('inactive_from')
        if cleaned.get('is_active'):
            cleaned['inactive_from'] = None
        else:
            if not inactive_from:
                cleaned['inactive_from'] = today
            elif inactive_from > today:
                self.add_error('inactive_from', 'Inactive-from date cannot be in the future.')
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
    grade = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Grade...',
        }),
    )
    work_location = forms.ChoiceField(
        required=False,
        choices=[('', 'Office / Site')] + list(Employee.WORK_LOCATION_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
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
            'model', 'part_number', 'tag_number', 'item_description',
            'accessories', 'software_installed',
            'invoice_number', 'employee_name', 'department', 'designation',
            'handover_date', 'handover_by', 'condition', 'return_date',
            'return_to', 'quantity', 'purchase_date', 'price',
            'planned_life', 'in_stock',
            'is_decommissioned', 'decommissioned_on', 'decommission_reason',
        ]
        widgets = {
            'asset_name': forms.TextInput(attrs={'class': 'form-control'}),
            'asset_type': forms.TextInput(attrs={'class': 'form-control', 'list': 'asset-type-list', 'placeholder': 'e.g. Laptop, Monitor'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control'}),
            'specifications': forms.TextInput(attrs={'class': 'form-control'}),
            'model': forms.TextInput(attrs={'class': 'form-control'}),
            'part_number': forms.TextInput(attrs={'class': 'form-control'}),
            'tag_number': forms.TextInput(attrs={'class': 'form-control'}),
            'item_description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'accessories': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'software_installed': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
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
            'is_decommissioned': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'decommissioned_on': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'decommission_reason': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Dead / beyond repair'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone
        self.fields['decommissioned_on'].required = False
        self.fields['decommission_reason'].required = False
        self.fields['decommissioned_on'].widget.attrs['max'] = timezone.localdate().isoformat()
    
    def clean_employee_name(self):
        return validate_name_field(self.cleaned_data.get('employee_name'), 'Employee name')

    def clean_department(self):
        return validate_title_field(self.cleaned_data.get('department'), 'Department')

    def clean_designation(self):
        return validate_title_field(self.cleaned_data.get('designation'), 'Designation')

    def clean_serial_number(self):
        return validate_code_field(self.cleaned_data.get('serial_number'), 'Serial number')

    def clean_invoice_number(self):
        return validate_code_field(self.cleaned_data.get('invoice_number'), 'Invoice number')
    
    def clean(self):
        from django.utils import timezone
        cleaned = super().clean()
        today = timezone.localdate()
        if cleaned.get('is_decommissioned'):
            cleaned['in_stock'] = False  # out of service can never be in stock
            if not cleaned.get('decommissioned_on'):
                cleaned['decommissioned_on'] = today
            elif cleaned['decommissioned_on'] > today:
                self.add_error('decommissioned_on', 'Date cannot be in the future.')
        else:
            cleaned['decommissioned_on'] = None
            cleaned['decommission_reason'] = ''
        return cleaned


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
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Statuses'), ('in_service', 'In Service'),
                 ('decommissioned', 'Out of Service')],
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


class MyDocumentUploadForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ['document_type', 'notes', 'file']
        widgets = {
            'document_type': forms.Select(attrs={'class': 'form-select', 'id': 'myDocTypeSelect'}),
            'notes': forms.TextInput(attrs={
                'class': 'form-control',
                'id': 'myDocNotesInput',
                'placeholder': 'e.g. Salary Certificate',
            }),
            'file': forms.ClearableFileInput(attrs={'class': 'form-control', 'id': 'myDocFileInput'}),
        }
        labels = {
            'notes': 'Please specify',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document_type'].choices = [
            c for c in EmployeeDocument.DOC_TYPE_CHOICES
            if c[0] in EmployeeDocument.SELF_SERVICE_DOC_TYPES
        ]

    def clean(self):
        cleaned_data = super().clean()
        document_type = cleaned_data.get('document_type')
        notes = cleaned_data.get('notes')
        if document_type == 'other' and not notes:
            self.add_error('notes', 'Please specify what this document is.')
        return cleaned_data

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


# ─── Leave Type & Holiday Forms ──────────────────────────────


class LeaveTypeForm(forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ['name', 'code', 'default_annual_days', 'site_default_annual_days', 'is_paid', 'color',
                  'requires_medical_certificate', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control'}),
            'default_annual_days': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'site_default_annual_days': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'color': forms.TextInput(attrs={'class': 'form-control'}),
            'requires_medical_certificate': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ExceptionGrantForm(forms.Form):
    """HR enters the employee's NEW total day count for a leave type/year,
    not a +/- delta — the view diffs it against their current effective
    total (LeaveEntitlement.effective_entitled_days) to derive the signed
    adjustment a LeaveExceptionGrant actually stores. A lower number reduces
    their balance, a higher number adds to it; there's no separate
    grant/deduct mode to pick, so a delta sign typo can't silently flip the
    outcome the way it could when the field itself was signed."""
    leave_type = forms.ModelChoiceField(
        queryset=LeaveType.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}))
    year = forms.IntegerField(widget=forms.NumberInput(attrs={'class': 'form-control'}))
    new_total_days = forms.DecimalField(
        min_value=Decimal('0'), label='New total days',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5'}))
    reason = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))


class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['date', 'name', 'category', 'is_active']
        widgets = {
            'date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
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


class SelfWFHForm(forms.Form):
    """An employee marking their own remote days from My Profile.

    Not a ModelForm on WFHRecord: that form exposes `employee`, which is
    exactly the field a self-service page must not accept from the browser.
    The dates are validated in hr.wfh_services, which is also what the
    register and any future entry point go through, so the rules cannot be
    enforced in one place and skipped in another.
    """
    start_date = forms.DateField(
        label='From',
        widget=forms.DateInput(attrs={'class': 'form-control form-control-sm',
                                      'type': 'date'}))
    end_date = forms.DateField(
        label='To', required=False,
        help_text='Leave blank for a single day.',
        widget=forms.DateInput(attrs={'class': 'form-control form-control-sm',
                                      'type': 'date'}))
    note = forms.CharField(
        label='Note', required=False, max_length=300,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-sm',
            'placeholder': 'Optional — e.g. where you are working from'}))

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'The end date cannot be before the start date.')
        return cleaned


def check_leave_balance(employee, leave_type, start_date, end_date, exclude_request_id=None):
    """Form-level fast-fail check: block a leave submission that would
    exceed the employee's remaining balance, or overlaps another leave they
    already have. Shared by every leave-creation entry point (admin
    log-on-behalf-of, legacy Add Leave, My Profile self-service) since they
    all now go through LeaveRequestForm.

    This is a UX convenience only — it runs unlocked, so a genuinely
    concurrent submission can still slip past it. The AUTHORITATIVE,
    race-safe check is hr.leave_services.validate_leave_submission(...,
    lock=True), which hr.leave_approval_services.submit_leave_request runs
    again (this time under a row lock) immediately before actually creating
    the LeaveRequest — this function just gives the form a chance to fail
    fast with a friendly message before that point."""
    from .leave_services import validate_leave_submission
    try:
        validate_leave_submission(employee, leave_type, start_date, end_date, lock=False,
                                  exclude_request_id=exclude_request_id)
    except ValueError as exc:
        raise forms.ValidationError(str(exc))


class LeaveRequestForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}))
    leave_type = forms.ModelChoiceField(
        queryset=LeaveType.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-select'}))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    employee_reason = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}))
    document = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={'class': 'form-control'}))

    def __init__(self, *args, fixed_employee=None, exclude_request_id=None,
                 allow_leave_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_employee = fixed_employee
        self.exclude_request_id = exclude_request_id
        if fixed_employee is not None:
            del self.fields['employee']
        # When editing a request whose leave type was later deactivated, keep
        # that exact type selectable so an unrelated date edit can't silently
        # switch it to a different (active) type.
        if allow_leave_type is not None:
            from django.db.models import Q
            self.fields['leave_type'].queryset = LeaveType.objects.filter(
                Q(is_active=True) | Q(pk=allow_leave_type.pk)).order_by('name')

    def clean(self):
        cleaned = super().clean()
        employee = self.fixed_employee or cleaned.get('employee')
        leave_type = cleaned.get('leave_type')
        start_date = cleaned.get('start_date')
        end_date = cleaned.get('end_date')
        document = cleaned.get('document')
        if start_date and end_date and end_date < start_date:
            self.add_error('end_date', 'End date cannot be before start date.')
        elif start_date and end_date and start_date.year != end_date.year:
            # LeaveEntitlement (and therefore check_leave_balance) is keyed
            # per calendar year — a request spanning two years can't be
            # validated or deducted against two different years' balances at
            # once. Silently checking only the start year's balance would
            # leave the end year's entitlement never consulted at all (a
            # real gap this closes), so require splitting at the boundary.
            self.add_error('end_date',
                'A single leave request cannot span two different years. '
                'Please submit this as two separate requests split at the year boundary.')
        elif employee and leave_type and start_date and end_date:
            if leave_type.requires_medical_certificate and not document:
                self.add_error('document',
                    f'A medical certificate/document is required for {leave_type.name} leave.')
            else:
                check_leave_balance(employee, leave_type, start_date, end_date,
                                    exclude_request_id=self.exclude_request_id)
        return cleaned


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


class EmployeeHierarchyForm(forms.Form):
    """Assigns ONE employee's main_manager/secondary_managers at a time — a
    per-row edit pattern (see OrgChartView), deliberately not a giant
    multi-employee single-submit form."""
    main_manager = forms.ModelChoiceField(
        queryset=Employee.objects.filter(is_active=True), required=False,
        widget=forms.Select(attrs={'class': 'form-select'}))
    secondary_managers = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(is_active=True), required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': '5'}))

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        # An employee cannot be their own manager — exclude themselves from
        # both pickers. clean() on the Employee model can't validate the
        # secondary_managers M2M (Django limitation — see Employee.clean()'s
        # own docstring), so self-assignment prevention for BOTH fields
        # happens here, at the form layer.
        if employee is not None:
            self.fields['main_manager'].queryset = self.fields['main_manager'].queryset.exclude(pk=employee.pk)
            self.fields['secondary_managers'].queryset = self.fields['secondary_managers'].queryset.exclude(pk=employee.pk)

    def clean_main_manager(self):
        main_manager = self.cleaned_data.get('main_manager')
        if self.employee and main_manager and main_manager.pk == self.employee.pk:
            raise forms.ValidationError('An employee cannot be their own manager.')
        return main_manager

    def clean(self):
        """Dry-runs Employee.clean()'s cycle-detection logic against a
        throwaway, unsaved Employee instance (same pk, candidate
        main_manager_id) instead of mutating/saving self.employee — so a
        circular main_manager assignment surfaces here as an ordinary form
        error, and self.employee is never touched unless the whole form is
        valid. This is preferred over calling self.employee.full_clean() in
        save(): full_clean() would also run Employee's per-field required-
        ness validation (e.g. iqama_number) on a real, saved employee whose
        other fields this form never touched and has no data for in
        cleaned_data, which would raise unrelated, confusing errors having
        nothing to do with the hierarchy assignment this form actually
        makes. Calling plain .clean() (via this disposable candidate) runs
        only the cycle-detection logic, which is exactly what needs
        (re-)checking here.
        """
        cleaned = super().clean()
        main_manager = cleaned.get('main_manager')
        if self.employee is not None:
            candidate = Employee(
                pk=self.employee.pk,
                main_manager_id=(main_manager.pk if main_manager else None))
            try:
                candidate.clean()
            except ValidationError as e:
                message_dict = getattr(e, 'message_dict', None)
                if message_dict:
                    for field, msgs in message_dict.items():
                        target = field if field in self.fields else 'main_manager'
                        for msg in msgs:
                            self.add_error(target, msg)
                else:
                    for msg in e.messages:
                        self.add_error('main_manager', msg)
        return cleaned

    def save(self):
        """Applies main_manager and secondary_managers. Cycle-detection for
        main_manager already ran (against a throwaway candidate) in clean()
        above, so by the time save() is called the assignment is known-safe
        — no need to re-validate against the real self.employee here."""
        self.employee.main_manager = self.cleaned_data.get('main_manager')
        self.employee.save(update_fields=['main_manager', 'updated_at'])
        self.employee.secondary_managers.set(self.cleaned_data.get('secondary_managers') or [])


class AttendanceExceptionForm(forms.Form):
    """Self-service 'report an exception' form — no `employee` field, the view
    supplies the employee (no admin log-on-behalf-of for this feature)."""
    event_date = forms.DateField(widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}))
    event_start_time = forms.TimeField(widget=forms.TimeInput(attrs={'class': 'form-control', 'type': 'time'}))
    reason_category = forms.ChoiceField(choices=AttendanceException.REASON_CHOICES,
                                        widget=forms.Select(attrs={'class': 'form-select'}))
    custom_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    employee_comment = forms.CharField(
        required=False, label='Additional comment (optional)',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('reason_category') == 'other' and not (cleaned.get('custom_reason') or '').strip():
            self.add_error('custom_reason', 'A custom reason is required when "Other" is selected.')
        return cleaned
