from django import forms
from django.forms import inlineformset_factory
from .models import (
    PurchaseOrder, PurchaseOrderItem,
    DeliveryNote, DeliveryNoteItem,
    InventoryReport, InventoryItem,
    FRCReport, FRCEntry, FRCInventory,
)
from projects.models import Project


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = [
            'po_date', 'po_number', 'cost_center', 'status',
            'vendor_name', 'vendor_contact_person', 'vendor_contact_email', 'vendor_contact_tel',
            'po_issued_by', 'issuer_email',
            'project', 'project_name', 'end_user', 'mr_item_number',
            'delivery_incoterms', 'delivery_location',
            'lead_time', 'payment_terms_text', 'warranty',
            'discount_rate', 'vat_rate',
            'terms_and_conditions',
        ]
        widgets = {
            'po_date': forms.DateInput(attrs={'type': 'date'}),
            'terms_and_conditions': forms.Textarea(attrs={'rows': 8}),
            'payment_terms_text': forms.Textarea(attrs={'rows': 3, 'placeholder': 'e.g. 30% Advance\n70% upon Delivery'}),
            'discount_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g. 5 for 5%'}),
            'vat_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'placeholder': 'e.g. 15 for 15%'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs['class'] = 'form-control'

        # On create, prefill PO Issuer with the current user's name + email so
        # the common case is a single click. Update mode preserves whatever
        # was saved before.
        if self.user and not self.instance.pk:
            full_name = self.user.get_full_name() or self.user.username
            if not self.initial.get('po_issued_by'):
                self.initial['po_issued_by'] = full_name
            if not self.initial.get('issuer_email') and self.user.email:
                self.initial['issuer_email'] = self.user.email

        self.fields['project'].required = False
        self.fields['project'].queryset = Project.objects.select_related('region').all()

        if self.user:
            if self.user.is_super_admin_user:
                pass
            elif getattr(self.user, 'is_procurement_user', False):
                # Procurement only ever issues POs against Won projects.
                self.fields['project'].queryset = Project.objects.filter(
                    region=self.user.region,
                    status__category='won',
                )
            elif self.user.is_admin_user or self.user.is_manager_user:
                self.fields['project'].queryset = Project.objects.filter(
                    region=self.user.region
                )
            else:
                self.fields['project'].queryset = Project.objects.filter(
                    owner=self.user
                )


class PurchaseOrderItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = [
            'serial_number', 'system', 'make_model', 'description',
            'quantity', 'uom', 'rate_per_unit', 'remarks',
            'po_value_usd', 'advance_payment_sar', 'delivery_status', 'scm',
            'order',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
            'remarks': forms.Textarea(attrs={'rows': 1}),
            'delivery_status': forms.Textarea(attrs={'rows': 1, 'placeholder': 'Delivered HO / At LNA-HO ...'}),
            'system': forms.TextInput(attrs={
                'list': 'po-system-suggestions',
                'placeholder': 'System',
                'autocomplete': 'off',
            }),
            'po_value_usd': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'USD/EUR'}),
            'advance_payment_sar': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'Advance SAR'}),
            'scm': forms.TextInput(attrs={'placeholder': 'ST / ZH', 'maxlength': '10'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control form-control-sm'
            else:
                field.widget.attrs['class'] = 'form-control form-control-sm'

    def has_changed(self):
        """An unsaved row is only considered "real" if the user typed
        something in the description.

        Without this, the empty extra row that Django adds at the end of
        the formset always reports has_changed=True (because the model
        defaults for quantity/uom/rate render as empty strings in the
        widget), which makes the row fail required-field validation and
        blocks the entire form save — including DELETE flags on saved rows.
        """
        if not self.instance.pk:
            description = (self.data.get(self.add_prefix('description')) or '').strip()
            if not description:
                return False
        return super().has_changed()


POItemFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderItem,
    form=PurchaseOrderItemForm,
    extra=0,
    can_delete=True,
)


class POFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Search PO number, vendor, project...'
        })
    )
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'All Statuses')] + PurchaseOrder.STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )


# ─── Delivery Note Forms ─────────────────────────────────────

class DeliveryNoteForm(forms.ModelForm):
    class Meta:
        model = DeliveryNote
        fields = [
            'sold_to_company', 'sold_to_address', 'delivery_address',
            'attention', 'mobile', 'email', 'date',
            'project_title', 'leap_po_number', 'client_po_number', 'dn_number',
            'project', 'purchase_order',
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'sold_to_address': forms.Textarea(attrs={'rows': 2}),
            'delivery_address': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control'
            else:
                field.widget.attrs['class'] = 'form-control'

        self.fields['project'].required = False
        self.fields['purchase_order'].required = False
        self.fields['project'].queryset = Project.objects.select_related('region').all()
        self.fields['purchase_order'].queryset = PurchaseOrder.objects.all()

        if self.user:
            if self.user.is_super_admin_user:
                pass
            elif self.user.is_admin_user or self.user.is_manager_user:
                self.fields['project'].queryset = Project.objects.filter(region=self.user.region)
            else:
                self.fields['project'].queryset = Project.objects.filter(owner=self.user)


class DeliveryNoteItemForm(forms.ModelForm):
    class Meta:
        model = DeliveryNoteItem
        fields = ['serial_number', 'make_part_number', 'description', 'quantity', 'uom', 'remarks', 'order']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
            'remarks': forms.Textarea(attrs={'rows': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control form-control-sm'
            else:
                field.widget.attrs['class'] = 'form-control form-control-sm'


DNItemFormSet = inlineformset_factory(
    DeliveryNote,
    DeliveryNoteItem,
    form=DeliveryNoteItemForm,
    extra=1,
    can_delete=True,
)


# ─── Inventory Report Forms ──────────────────────────────────

class InventoryReportForm(forms.ModelForm):
    class Meta:
        model = InventoryReport
        fields = ['title', 'warehouse_location', 'project']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['project'].required = False
        self.fields['project'].queryset = Project.objects.select_related('region').all()
        if self.user:
            if self.user.is_super_admin_user:
                pass
            elif self.user.is_admin_user or self.user.is_manager_user:
                self.fields['project'].queryset = Project.objects.filter(region=self.user.region)
            else:
                self.fields['project'].queryset = Project.objects.filter(owner=self.user)


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = [
            'serial_number', 'item_code', 'name', 'model_number', 'make',
            'category', 'status', 'unit',
            'quantity_received', 'quantity_issued', 'balance_qty', 'min_stock_level',
            'unit_cost', 'tag', 'rack_location',
            'received_date', 'handover_to', 'handover_date', 'project_ref',
            'po_reference', 'remarks', 'order',
        ]
        widgets = {
            'received_date': forms.DateInput(attrs={'type': 'date'}),
            'handover_date': forms.DateInput(attrs={'type': 'date'}),
            'remarks': forms.Textarea(attrs={'rows': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select form-select-sm'
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control form-control-sm'
            else:
                field.widget.attrs['class'] = 'form-control form-control-sm'


InventoryItemFormSet = inlineformset_factory(
    InventoryReport,
    InventoryItem,
    form=InventoryItemForm,
    extra=1,
    can_delete=True,
)


# ─── FRC / PPE Forms ─────────────────────────────────────────

class FRCReportForm(forms.ModelForm):
    class Meta:
        model = FRCReport
        fields = ['title', 'project']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
        self.fields['project'].required = False
        self.fields['project'].queryset = Project.objects.select_related('region').all()
        if self.user:
            if self.user.is_super_admin_user:
                pass
            elif self.user.is_admin_user or self.user.is_manager_user:
                self.fields['project'].queryset = Project.objects.filter(region=self.user.region)
            else:
                self.fields['project'].queryset = Project.objects.filter(owner=self.user)


class FRCEntryForm(forms.ModelForm):
    class Meta:
        model = FRCEntry
        fields = [
            'serial_number', 'employee_name', 'designation', 'project_name',
            'shirt_size', 'shirt_qty', 'pants_size', 'pants_qty',
            'safety_glasses', 'safety_shoes', 'shoes_qty', 'safety_helmet',
            'receiving_date', 'handed_to', 'order',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control form-control-sm'


FRCEntryFormSet = inlineformset_factory(
    FRCReport,
    FRCEntry,
    form=FRCEntryForm,
    extra=1,
    can_delete=True,
)


# ─── FRC Inventory Form ──────────────────────────────────────

class FRCInventoryForm(forms.ModelForm):
    class Meta:
        model = FRCInventory
        fields = [
            'item_type', 'size', 'color', 'description', 'supplier',
            'total_purchased', 'total_issued', 'damaged_lost', 'min_stock',
            'unit_cost', 'last_restocked',
        ]
        widgets = {
            'last_restocked': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
