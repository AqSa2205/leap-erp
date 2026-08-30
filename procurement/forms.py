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
            'project', 'project_name', 'end_user', 'mr_item_number', 'mr_revision',
            'delivery_incoterms', 'delivery_location',
            'lead_time', 'payment_terms_text', 'warranty',
            'currency', 'discount_rate', 'vat_rate',
            'terms_and_conditions',
        ]
        widgets = {
            'po_date': forms.DateInput(attrs={'type': 'date'}),
            'terms_and_conditions': forms.Textarea(attrs={'rows': 8, 'class': 'tinymce-editor'}),
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

        # A client-acknowledged PO is read-only. The fields are disabled so the
        # page reads honestly, and clean() refuses regardless — a disabled
        # widget is a hint to the browser, not a rule the server keeps.
        if self.instance.pk and self.instance.is_locked:
            for field in self.fields.values():
                field.disabled = True

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

        if self.user and not self.user.is_super_admin_user:
            if getattr(self.user, 'is_procurement_user', False):
                # Procurement only ever issues POs against Won projects.
                self.fields['project'].queryset = Project.objects.filter(
                    region=self.user.region,
                    status__category='won',
                ).select_related('region')
            else:
                self.fields['project'].queryset = Project.objects.filter(
                    region=self.user.region
                ).select_related('region')

    def clean(self):
        """Refuse every edit to a locked PO.

        Belt and braces with the disabled widgets above: a POST can arrive
        without ever rendering the form.
        """
        cleaned = super().clean()
        if self.instance.pk and self.instance.is_locked:
            raise forms.ValidationError(
                'This purchase order has been acknowledged by the client and '
                'is locked. A super admin must release it before it can be '
                'edited.'
            )
        return cleaned


class POStageApproverForm(forms.ModelForm):
    """Map one approval stage to the person who signs it.

    Exists because APPROVAL_STAGES names its signers as plain text, which is
    enough to print on a PDF and useless for sending them an email.
    """
    class Meta:
        from .models import POStageApprover
        model = POStageApprover
        fields = ['stage', 'user']
        widgets = {
            'stage': forms.Select(attrs={'class': 'form-select'}),
            'user': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        self.fields['user'].queryset = User.objects.filter(
            is_active=True).order_by('first_name', 'last_name', 'username')
        # Naming somebody who cannot sign the stage would produce an email
        # inviting them to press a button that refuses them.
        self.fields['user'].help_text = (
            'They must also hold the role that may sign this stage — this '
            'decides who is told, not who is allowed.')

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
            'quantity': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'rate_per_unit': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'po_value_usd': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'USD/EUR'}),
            'advance_payment_sar': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'placeholder': 'Advance SAR'}),
            'scm': forms.TextInput(attrs={'placeholder': 'ST / ZH', 'maxlength': '10'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Description is required at the model level, but an EXISTING item that
        # happens to have a blank description (common for BOM-seeded or sparse
        # POs) must not block editing the rest of the PO — that silently
        # discarded every change. New rows still need a description to count as
        # "real" (see has_changed), so blank rows are dropped, never saved empty.
        self.fields['description'].required = False
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = 'form-control form-control-sm'
            else:
                field.widget.attrs['class'] = 'form-control form-control-sm'

    def clean_quantity(self):
        value = self.cleaned_data.get('quantity')
        if value is not None and value < 0:
            raise forms.ValidationError('Quantity cannot be negative.')
        return value

    def clean_rate_per_unit(self):
        value = self.cleaned_data.get('rate_per_unit')
        if value is not None and value < 0:
            raise forms.ValidationError('Rate/Unit cannot be negative.')
        return value

    def clean_po_value_usd(self):
        value = self.cleaned_data.get('po_value_usd')
        if value is not None and value < 0:
            raise forms.ValidationError('PO Value (USD/EUR) cannot be negative.')
        return value

    def clean_advance_payment_sar(self):
        value = self.cleaned_data.get('advance_payment_sar')
        if value is not None and value < 0:
            raise forms.ValidationError('Advance Payment (SAR) cannot be negative.')
        return value

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
            'project_title': forms.TextInput(attrs={'autocomplete': 'off'}),
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

        if self.user and not self.user.is_super_admin_user:
            self.fields['project'].queryset = Project.objects.filter(
                region=self.user.region
            ).select_related('region')

    def clean_sold_to_company(self):
        value = self.cleaned_data.get('sold_to_company', '')
        if value and value.strip().isdigit():
            raise forms.ValidationError(
                "Company name cannot be numbers only. Please enter a valid "
                "company name (e.g., 'Leap Ltd', 'Leap Company')."
            )
        return value

    def clean_delivery_address(self):
        value = self.cleaned_data.get('delivery_address', '')
        if value and len(value.strip()) < 10:
            raise forms.ValidationError(
                "Delivery address must be at least 10 characters."
            )
        return value

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

    def has_changed(self):
        # An unsaved row without a description is treated as untouched so a
        # blank extra row never blocks validation (and never blocks DELETE
        # flags on saved rows from being applied).
        if not self.instance.pk:
            description = (self.data.get(self.add_prefix('description')) or '').strip()
            if not description:
                return False
        return super().has_changed()


DNItemFormSet = inlineformset_factory(
    DeliveryNote,
    DeliveryNoteItem,
    form=DeliveryNoteItemForm,
    extra=0,
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
        if self.user and not self.user.is_super_admin_user:
            self.fields['project'].queryset = Project.objects.filter(
                region=self.user.region
            ).select_related('region')


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
        if self.user and not self.user.is_super_admin_user:
            self.fields['project'].queryset = Project.objects.filter(
                region=self.user.region
            ).select_related('region')


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
