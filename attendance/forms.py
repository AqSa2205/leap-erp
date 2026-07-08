import re

from django import forms

from hr.models import Employee
from .models import OfficeNetwork, RegisteredDevice

_BSSID_RE = re.compile(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$')


class OfficeNetworkForm(forms.ModelForm):
    class Meta:
        model = OfficeNetwork
        fields = ['bssid', 'label']
        widgets = {
            'bssid': forms.TextInput(attrs={'class': 'form-control form-control-sm',
                                            'placeholder': 'a1:b2:c3:d4:e5:f6'}),
            'label': forms.TextInput(attrs={'class': 'form-control form-control-sm',
                                            'placeholder': 'HO 2nd-floor AP'}),
        }

    def clean_bssid(self):
        value = (self.cleaned_data.get('bssid') or '').strip().lower()
        if not _BSSID_RE.match(value):
            raise forms.ValidationError('Enter a BSSID like a1:b2:c3:d4:e5:f6 (six hex pairs).')
        return value


class RegisteredDeviceForm(forms.ModelForm):
    class Meta:
        model = RegisteredDevice
        fields = ['employee', 'serial_number', 'label']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control form-control-sm',
                                                    'placeholder': 'serial (auto from asset)'}),
            'label': forms.TextInput(attrs={'class': 'form-control form-control-sm',
                                            'placeholder': 'hostname'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['employee'].queryset = Employee.objects.filter(
            is_active=True).order_by('full_name')
