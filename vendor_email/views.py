from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from costing.models import CostingSheet

from .models import VendorEmailMessage
from .services import resolve_manually
from django.contrib import messages
from django.shortcuts import redirect



@login_required
def triage(request):
    from .matching import suggest_matching_sheet
    from .services import process_new_messages
    try:
        process_new_messages()
    except Exception:
        pass  # mailbox check failed silently — don't block the page from loading
    unmatched = list(VendorEmailMessage.objects.filter(status='unmatched'))
    for m in unmatched:
        sheet, reason = suggest_matching_sheet(m.sender_email, m.extracted_reference)
        m.suggested_sheet_id = sheet.pk if sheet else None
        m.suggested_sheet_title = sheet.title if sheet else None
        m.suggested_reason = reason
    sheets = CostingSheet.objects.order_by('-updated_at')
    return render(request, 'vendor_email/triage.html', {'unmatched': unmatched, 'sheets': sheets})


@login_required
def resolve(request, pk):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    message = get_object_or_404(VendorEmailMessage, pk=pk, status='unmatched')
    sheet = get_object_or_404(CostingSheet, pk=request.POST.get('sheet_id'))
    resolve_manually(message, sheet, request.user)
    return JsonResponse({'ok': True, 'sheet_title': sheet.title})





@login_required
def simulate(request):
    if request.method != 'POST':
        return redirect('vendor_email:triage')
    from .services import process_new_messages
    scenario = request.POST.get('scenario', 'matched')
    created = process_new_messages(scenario=scenario)
    if created:
        msg = created[0]
        if msg.status == 'matched_auto':
            messages.success(request, f'Simulated email auto-attached to "{msg.matched_sheet.title}".')
        else:
            messages.warning(request, f'Simulated email from {msg.sender_email} needs review — {msg.match_reason}')
    return redirect('vendor_email:triage')


