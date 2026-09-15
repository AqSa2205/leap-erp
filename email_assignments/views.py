from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from costing.models import RevisionMailbox
from projects.models import MonitoredMailbox
from proposals.models import (
    ProposalMailbox, ProposalDepartmentFeature, PROPOSAL_LOCKABLE_DEPARTMENTS,
)

from .forms import (
    ProposalMailboxAssignForm, RevisionMailboxAssignForm, MonitoredMailboxAssignForm,
)


def _is_email_admin(user):
    """ERP Admin + Super Admin manage everything on this page — mailbox
    assignment (all three tabs) and the department export-lock toggle
    alike. Same two roles that already see the sidebar's Administration
    section this page lives in."""
    return bool(user.is_super_admin_user or user.is_erp_admin_user)


def _forbidden(request):
    messages.error(request, 'You do not have access to Email Assigning.')
    return redirect('dashboard:index')


@login_required
def mailbox_list(request):
    if not _is_email_admin(request.user):
        return _forbidden(request)

    # Distinct auto_id per form: all three tabs' markup is present in the
    # DOM at once (only CSS/JS hides the inactive ones), so leaving Django's
    # default auto_id would render three <select id="id_owner"> elements on
    # one page — invalid duplicate HTML ids, and each tab's <label for=...>
    # would resolve to the FIRST tab's select rather than its own.
    context = {
        'proposal_mailboxes': ProposalMailbox.objects.select_related(
            'owner', 'assigned_by', 'revoked_by').all(),
        'proposal_form': ProposalMailboxAssignForm(auto_id='id_proposal_%s'),
        'revision_mailboxes': RevisionMailbox.objects.select_related(
            'owner', 'assigned_by', 'revoked_by').all(),
        'revision_form': RevisionMailboxAssignForm(auto_id='id_revision_%s'),
        'monitored_mailboxes': MonitoredMailbox.objects.select_related(
            'owner', 'assigned_by', 'revoked_by').all(),
        'monitored_form': MonitoredMailboxAssignForm(auto_id='id_monitored_%s'),
    }

    # The "Require Email Attachment" export-lock toggle is Technical
    # Proposal specific — Costing and Commercial Pipeline have no
    # equivalent concept, so it only ever shows on that one tab.
    existing = {row.department: row for row in ProposalDepartmentFeature.objects.all()}
    context['department_locks'] = [
        existing.get(code) or ProposalDepartmentFeature(
            department=code, requires_client_email_to_export=False)
        for code, _label in PROPOSAL_LOCKABLE_DEPARTMENTS
    ]
    return render(request, 'email_assignments/mailbox_list.html', context)


@login_required
@require_POST
def toggle_department_lock(request, department):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    if department not in dict(PROPOSAL_LOCKABLE_DEPARTMENTS):
        return _forbidden(request)
    row, _created = ProposalDepartmentFeature.objects.get_or_create(department=department)
    row.requires_client_email_to_export = not row.requires_client_email_to_export
    row.updated_by = request.user
    row.save(update_fields=['requires_client_email_to_export', 'updated_by', 'updated_at'])
    if row.requires_client_email_to_export:
        messages.success(
            request, f'{row.get_department_display()} proposals now require a linked '
                     'client email before they can export as DOCX.')
    else:
        messages.success(
            request, f'{row.get_department_display()} proposals can export as DOCX freely again.')
    return redirect('email_assignments:list')


# ─── Shared mailbox-mutation logic, reused by all three tabs ───────────────
#
# assign/toggle/delete are identical in shape across ProposalMailbox,
# RevisionMailbox and MonitoredMailbox — same fields, same audit trail, same
# "address always comes from the employee's own account" rule. Kept as one
# implementation parameterised by (model, form_class, success noun) rather
# than tripling the same four functions, since email_assignments is the one
# app that's meant to depend on all three models directly.

def _assign_mailbox(request, form_class, feature_label):
    form = form_class(request.POST)
    if form.is_valid():
        mailbox = form.save(commit=False)
        # Always the employee's own address on file, never whatever an
        # admin might type — see _MailboxAssignFormBase's docstring.
        mailbox.email_address = mailbox.owner.email
        mailbox.assigned_by = request.user
        mailbox.save()
        messages.success(
            request,
            f'{mailbox.owner.get_full_name() or mailbox.owner.username} can now link '
            f'client emails from {mailbox.email_address} on {feature_label}.')
    else:
        for error in form.non_field_errors():
            messages.error(request, error)
        for field in form:
            for error in field.errors:
                messages.error(request, f'{field.label}: {error}')
    return redirect('email_assignments:list')


def _toggle_mailbox(request, model, pk):
    mailbox = get_object_or_404(model, pk=pk)
    mailbox.is_active = not mailbox.is_active
    if mailbox.is_active:
        # Reactivating: the old revoke record no longer describes the
        # mailbox's current state, so clear it rather than leave a stale
        # "revoked by" hanging around on an active row.
        mailbox.revoked_by = None
        mailbox.revoked_at = None
        mailbox.save(update_fields=['is_active', 'revoked_by', 'revoked_at'])
        messages.success(request, f'Mailbox access restored for {mailbox.owner}.')
    else:
        mailbox.revoked_by = request.user
        mailbox.revoked_at = timezone.now()
        mailbox.save(update_fields=['is_active', 'revoked_by', 'revoked_at'])
        messages.success(request, f'Mailbox access revoked for {mailbox.owner}.')
    return redirect('email_assignments:list')


def _delete_mailbox(request, model, pk):
    mailbox = get_object_or_404(model, pk=pk)
    owner = mailbox.owner
    mailbox.delete()
    messages.success(request, f'Removed the mailbox assignment for {owner}.')
    return redirect('email_assignments:list')


# ─── Technical Proposals tab ────────────────────────────────────────────────

@login_required
@require_POST
def assign_proposal_mailbox(request):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _assign_mailbox(request, ProposalMailboxAssignForm, 'Technical Proposals')


@login_required
@require_POST
def toggle_proposal_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _toggle_mailbox(request, ProposalMailbox, pk)


@login_required
@require_POST
def delete_proposal_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _delete_mailbox(request, ProposalMailbox, pk)


# ─── Costing tab ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def assign_revision_mailbox(request):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _assign_mailbox(request, RevisionMailboxAssignForm, 'costing revisions')


@login_required
@require_POST
def toggle_revision_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _toggle_mailbox(request, RevisionMailbox, pk)


@login_required
@require_POST
def delete_revision_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _delete_mailbox(request, RevisionMailbox, pk)


# ─── Commercial Pipeline tab ─────────────────────────────────────────────────

@login_required
@require_POST
def assign_monitored_mailbox(request):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _assign_mailbox(request, MonitoredMailboxAssignForm, 'Commercial Pipeline')


@login_required
@require_POST
def toggle_monitored_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _toggle_mailbox(request, MonitoredMailbox, pk)


@login_required
@require_POST
def delete_monitored_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    return _delete_mailbox(request, MonitoredMailbox, pk)
