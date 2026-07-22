import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.utils.module_loading import import_string
from django.urls import reverse

from .models import FormDraft
from .registry import FORM_REGISTRY

MAX_PAYLOAD_CHARS = 50000


@login_required
@require_POST
def save_draft(request):
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'bad request'}, status=400)

    form_key = body.get('form_key')
    if form_key not in FORM_REGISTRY:
        return JsonResponse({'error': 'unknown form'}, status=400)

    raw_data = body.get('data') or {}
    if not isinstance(raw_data, dict):
        return JsonResponse({'error': 'bad data'}, status=400)

    form_class = import_string(FORM_REGISTRY[form_key]['form_path'])
    allowed_keys = set(form_class().fields.keys())
    cleaned = {k: v for k, v in raw_data.items() if k in allowed_keys}

    if len(json.dumps(cleaned)) > MAX_PAYLOAD_CHARS:
        return JsonResponse({'error': 'too large'}, status=400)

    object_id = body.get('object_id')

    FormDraft.objects.update_or_create(
        user=request.user,
        form_key=form_key,
        object_id=object_id,
        defaults={'data': cleaned},
    )
    return JsonResponse({'ok': True})


@login_required
def check_drafts(request):
    drafts = FormDraft.objects.filter(user=request.user).order_by('-updated_at')
    items = []
    for d in drafts:
        info = FORM_REGISTRY.get(d.form_key)
        if not info:
            continue
        items.append({
            'id': d.pk,
            'form_key': d.form_key,
            'updated_at': d.updated_at.strftime('%d %b %Y, %H:%M'),
            'resume_url': reverse(info['resume_url_name']) + f'?resume_draft={d.pk}',
        })
    return JsonResponse({'count': len(items), 'drafts': items})


@login_required
@require_POST
def discard_draft(request, pk):
    draft = get_object_or_404(FormDraft, pk=pk, user=request.user)
    draft.delete()
    return JsonResponse({'ok': True})