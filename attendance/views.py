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
                d = dev_form.save()
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
        'devices': RegisteredDevice.objects.select_related('employee').all(),
    })


@login_required
def tokens_export(request):
    if not _can_manage(request.user):
        return redirect('dashboard:index')
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="attendance_device_tokens.csv"'
    writer = csv.writer(resp)
    writer.writerow(['employee', 'iqama_number', 'device_label', 'token', 'is_active'])
    for d in RegisteredDevice.objects.select_related('employee').all():
        writer.writerow([d.employee.full_name, d.employee.iqama_number,
                         d.label, d.token, 'yes' if d.is_active else 'no'])
    return resp
