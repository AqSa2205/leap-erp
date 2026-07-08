"""In-app registration UI for Wi-Fi attendance — office access points and
employee devices — plus a token-map CSV export for provisioning agents.
Admin / super-admin only (registration is an IT/HR-admin task)."""
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import OfficeNetworkForm, RegisteredDeviceForm
from .models import OfficeNetwork, RegisteredDevice


def _can_manage(user):
    return bool(getattr(user, 'is_authenticated', False)
                and (user.is_super_admin_user or user.is_admin_user))


@login_required
def manage(request):
    if not _can_manage(request.user):
        messages.error(request, 'Wi-Fi attendance setup is limited to administrators.')
        return redirect('dashboard:index')

    net_form = OfficeNetworkForm()
    dev_form = RegisteredDeviceForm()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_network':
            net_form = OfficeNetworkForm(request.POST)
            if net_form.is_valid():
                net_form.save()
                messages.success(request, 'Office access point added.')
                return redirect('attendance_ui:attendance_devices')
        elif action == 'add_device':
            dev_form = RegisteredDeviceForm(request.POST)
            if dev_form.is_valid():
                d = dev_form.save(commit=False)
                # Link the chosen HR asset (dropdown filtered to the employee's
                # own assets, client-side) and auto-fill the serial from it.
                from hr.models import Asset
                asset = Asset.objects.filter(pk=request.POST.get('asset_id') or 0).first()
                if asset:
                    d.asset = asset
                    if not d.serial_number:
                        d.serial_number = asset.serial_number or ''
                d.save()
                messages.success(request, f'Device registered for {d.employee} — token generated.')
                return redirect('attendance_ui:attendance_devices')
        elif action == 'toggle_network':
            n = get_object_or_404(OfficeNetwork, pk=request.POST.get('pk'))
            n.is_active = not n.is_active
            n.save(update_fields=['is_active'])
            return redirect('attendance_ui:attendance_devices')
        elif action == 'delete_network':
            OfficeNetwork.objects.filter(pk=request.POST.get('pk')).delete()
            messages.success(request, 'Access point removed.')
            return redirect('attendance_ui:attendance_devices')
        elif action == 'toggle_device':
            d = get_object_or_404(RegisteredDevice, pk=request.POST.get('pk'))
            d.is_active = not d.is_active
            d.save(update_fields=['is_active'])
            return redirect('attendance_ui:attendance_devices')
        elif action == 'delete_device':
            RegisteredDevice.objects.filter(pk=request.POST.get('pk')).delete()
            messages.success(request, 'Device removed.')
            return redirect('attendance_ui:attendance_devices')

    return render(request, 'attendance/manage.html', {
        'net_form': net_form,
        'dev_form': dev_form,
        'networks': OfficeNetwork.objects.all(),
        'devices': RegisteredDevice.objects.select_related('employee', 'asset').all(),
        'emp_assets': _employee_assets_map(),
    })


def _employee_assets_map():
    """{employee_id: [{'id','name','serial'}, ...]} of each active employee's
    currently-held assets, so the device form can show only that person's own
    laptops (and auto-fill the serial). Covers both the AssetAssignment link
    and the legacy employee_name string."""
    from collections import defaultdict
    from hr.models import Asset, AssetAssignment, Employee

    out = defaultdict(list)
    seen = set()

    def add(emp_id, asset):
        key = (emp_id, asset.id)
        if key in seen:
            return
        seen.add(key)
        out[emp_id].append({'id': asset.id, 'name': asset.asset_name,
                            'serial': asset.serial_number or ''})

    # Proper FK link: active (unreturned) assignments.
    for a in (AssetAssignment.objects
              .filter(returned_at__isnull=True)
              .select_related('asset')):
        add(a.employee_id, a.asset)

    # Legacy: assets tagged only by employee_name.
    name_to_id = {(e.full_name or '').strip().lower(): e.id
                  for e in Employee.objects.filter(is_active=True)}
    for asset in Asset.objects.exclude(employee_name='').filter(is_decommissioned=False):
        emp_id = name_to_id.get((asset.employee_name or '').strip().lower())
        if emp_id:
            add(emp_id, asset)

    return {str(k): v for k, v in out.items()}


@login_required
def tokens_export(request):
    if not _can_manage(request.user):
        return redirect('dashboard:index')
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="attendance_device_tokens.csv"'
    writer = csv.writer(resp)
    writer.writerow(['employee', 'iqama_number', 'device_label', 'serial_number', 'token', 'is_active'])
    for d in RegisteredDevice.objects.select_related('employee').all():
        writer.writerow([d.employee.full_name, d.employee.iqama_number,
                         d.label, d.serial_number, d.token, 'yes' if d.is_active else 'no'])
    return resp
