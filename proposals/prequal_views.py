"""Prequalification v2 — a library of standard PDFs, selectively merged into a
single combined PDF per project submission.
"""
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from projects.models import Project
from .models import PrequalLibraryItem, PrequalSubmission


# ─── Access helpers ──────────────────────────────────────────────

def _can_use_prequal(user):
    return bool(getattr(user, 'is_authenticated', False) and (
        user.is_super_admin_user or user.is_admin_user or user.is_manager_user
        or user.is_erp_admin_user
        or getattr(user, 'is_proposal_team_user', False)))


def _can_manage_library(user):
    return bool(getattr(user, 'is_authenticated', False) and (
        user.is_super_admin_user or user.is_admin_user or user.is_erp_admin_user
        or getattr(user, 'is_proposal_head_user', False)))


def _visible_submissions(user):
    qs = PrequalSubmission.objects.select_related('project', 'created_by')
    # ERP Admin manages the Administration section company-wide — every submission.
    if user.is_super_admin_user or user.is_erp_admin_user:
        return qs
    if user.is_admin_user or user.is_manager_user or getattr(user, 'is_proposal_team_user', False):
        return qs.filter(Q(created_by=user) | Q(project__region=user.region)).distinct()
    return qs.filter(created_by=user)


# ─── PDF merge ───────────────────────────────────────────────────

def merge_prequal_pdfs(submission):
    """Merge the submission's selected library PDFs (in heading order) into one
    PDF. Returns (bytes, skipped_headings)."""
    from pypdf import PdfWriter, PdfReader
    writer = PdfWriter()
    skipped = []
    for item in submission.selected_in_order():
        try:
            item.pdf.open('rb')
            data = item.pdf.read()
            item.pdf.close()
            reader = PdfReader(BytesIO(data))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            skipped.append(item.heading)
    out = BytesIO()
    writer.write(out)
    return out.getvalue(), skipped


# ─── Submissions ─────────────────────────────────────────────────

@login_required
def prequal_list(request):
    if not _can_use_prequal(request.user):
        messages.error(request, 'You do not have access to Prequalification.')
        return redirect('proposals:dashboard')
    submissions = _visible_submissions(request.user)
    return render(request, 'proposals/prequal/list.html', {
        'submissions': submissions,
        'can_manage_library': _can_manage_library(request.user),
    })


@login_required
def prequal_create(request):
    if not _can_use_prequal(request.user):
        messages.error(request, 'Permission denied.')
        return redirect('proposals:prequal_list')
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        if not title:
            messages.error(request, 'A title is required.')
            return redirect('proposals:prequal_create')
        sub = PrequalSubmission.objects.create(
            title=title,
            client_name=(request.POST.get('client_name') or '').strip(),
            reference=(request.POST.get('reference') or '').strip(),
            project=_resolve_project(request.POST.get('project')),
            created_by=request.user)
        messages.success(request, 'Prequalification created — now select the documents.')
        return redirect('proposals:prequal_edit', pk=sub.pk)
    return render(request, 'proposals/prequal/form.html', {
        'projects': _project_choices(request.user),
    })


@login_required
def prequal_edit(request, pk):
    sub = get_object_or_404(_visible_submissions(request.user), pk=pk)
    if request.method == 'POST':
        sub.title = (request.POST.get('title') or sub.title).strip()
        sub.client_name = (request.POST.get('client_name') or '').strip()
        sub.reference = (request.POST.get('reference') or '').strip()
        sub.project = _resolve_project(request.POST.get('project'))
        # Cover-page title-block fields.
        for fld in ('dep_1', 'dep_2', 'dep_3', 'description_month', 'description_text',
                    'cover_date', 'revision', 'report_no', 'block_date'):
            setattr(sub, fld, (request.POST.get(fld) or '').strip())
        sub.save()
        ids = [int(x) for x in request.POST.getlist('items') if x.isdigit()]
        sub.selected_items.set(PrequalLibraryItem.objects.filter(id__in=ids))
        messages.success(request, 'Selection saved.')
        return redirect('proposals:prequal_detail', pk=sub.pk)

    selected_ids = set(sub.selected_items.values_list('id', flat=True))
    items = PrequalLibraryItem.objects.filter(is_active=True)
    return render(request, 'proposals/prequal/edit.html', {
        'sub': sub,
        'items': [{'item': it, 'selected': it.id in selected_ids} for it in items],
        'projects': _project_choices(request.user),
        'can_manage_library': _can_manage_library(request.user),
    })


@login_required
def prequal_detail(request, pk):
    sub = get_object_or_404(_visible_submissions(request.user), pk=pk)
    selected = list(sub.selected_in_order())
    missing = sub.selected_items.filter(Q(pdf__isnull=True) | Q(pdf='')).count()
    return render(request, 'proposals/prequal/detail.html', {
        'sub': sub, 'selected': selected, 'missing_count': missing,
    })


@login_required
@require_POST
def prequal_delete(request, pk):
    sub = get_object_or_404(_visible_submissions(request.user), pk=pk)
    sub.delete()
    messages.success(request, 'Prequalification deleted.')
    return redirect('proposals:prequal_list')


@login_required
def prequal_export(request, pk):
    sub = get_object_or_404(_visible_submissions(request.user), pk=pk)
    if not sub.selected_in_order().exists():
        messages.error(request, 'Select at least one document with a PDF before combining.')
        return redirect('proposals:prequal_edit', pk=sub.pk)
    # Branded combined PDF: cover + table of contents + a heading/divider page
    # before each document, then the merged document PDFs.
    from .prequal_export import build_prequal_combined_pdf
    data, skipped = build_prequal_combined_pdf(sub)
    # NOTE: do NOT add a messages.* here — this response is a PDF, which cannot
    # render the messages block, so the message would leak onto the next HTML
    # page the user opens. Skipped documents are surfaced on the detail page
    # instead (see prequal_detail's unreadable list).
    filename = (slugify(sub.reference or sub.title)[:80] or 'prequalification') + '.pdf'
    resp = HttpResponse(data, content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{filename}"'
    return resp


# ─── Library management ──────────────────────────────────────────

@login_required
def prequal_library(request):
    if not _can_manage_library(request.user):
        messages.error(request, 'Only admins manage the prequalification library.')
        return redirect('proposals:prequal_list')
    items = PrequalLibraryItem.objects.all()
    return render(request, 'proposals/prequal/library.html', {'items': items})


def _validate_pdf(upload):
    """Return an error string if the upload isn't a usable PDF, else ''."""
    if upload is None:
        return ''
    name = (upload.name or '').lower()
    ctype = (getattr(upload, 'content_type', '') or '').lower()
    if not (name.endswith('.pdf') or ctype == 'application/pdf'):
        return 'Only PDF files can be added to the library — please upload a .pdf.'
    if upload.size > 40 * 1024 * 1024:
        return 'PDF is too large (max 40 MB).'
    return ''


@login_required
@require_POST
def prequal_library_save(request, pk):
    if not _can_manage_library(request.user):
        messages.error(request, 'Permission denied.')
        return redirect('proposals:prequal_library')
    item = get_object_or_404(PrequalLibraryItem, pk=pk)
    upload = request.FILES.get('pdf')
    err = _validate_pdf(upload)
    if err:
        messages.error(request, f'{item.heading}: {err}')
        return redirect('proposals:prequal_library')
    item.heading = (request.POST.get('heading') or item.heading).strip()
    item.order = _to_int(request.POST.get('order'), item.order)
    item.is_active = request.POST.get('is_active') == '1'
    if upload:
        item.pdf = upload
    try:
        item.save()
    except Exception as e:
        messages.error(request, f'Could not save "{item.heading}": {e}')
        return redirect('proposals:prequal_library')
    messages.success(request, f'Saved "{item.heading}".')
    return redirect('proposals:prequal_library')


@login_required
@require_POST
def prequal_library_add(request):
    if not _can_manage_library(request.user):
        messages.error(request, 'Permission denied.')
        return redirect('proposals:prequal_library')
    heading = (request.POST.get('heading') or '').strip()
    if not heading:
        messages.error(request, 'Heading is required.')
        return redirect('proposals:prequal_library')
    upload = request.FILES.get('pdf')
    err = _validate_pdf(upload)
    if err:
        messages.error(request, err)
        return redirect('proposals:prequal_library')
    from django.db.models import Max
    nxt = (PrequalLibraryItem.objects.aggregate(m=Max('order'))['m'] or 0) + 1
    item = PrequalLibraryItem.objects.create(heading=heading, order=nxt)
    if upload:
        item.pdf = upload
        item.save()
    messages.success(request, f'Added "{heading}".')
    return redirect('proposals:prequal_library')


@login_required
@require_POST
def prequal_library_delete(request, pk):
    if not _can_manage_library(request.user):
        messages.error(request, 'Permission denied.')
        return redirect('proposals:prequal_library')
    item = get_object_or_404(PrequalLibraryItem, pk=pk)
    item.delete()
    messages.success(request, 'Library item deleted.')
    return redirect('proposals:prequal_library')


# ─── Small helpers ───────────────────────────────────────────────

def _to_int(raw, default=0):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _resolve_project(raw):
    if raw and str(raw).isdigit():
        return Project.objects.filter(pk=int(raw)).first()
    return None


def _project_choices(user):
    qs = Project.objects.select_related('region').order_by('-id')
    company_wide = user.is_super_admin_user or user.is_erp_admin_user
    if not company_wide and getattr(user, 'region_id', None):
        qs = qs.filter(region_id=user.region_id)
    return qs[:500]
