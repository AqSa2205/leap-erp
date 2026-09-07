from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from proposals.models import (
    ProposalMailbox, ProposalDepartmentFeature, PROPOSAL_LOCKABLE_DEPARTMENTS,
)

from .forms import ProposalMailboxAssignForm


def _is_email_admin(user):
    """ERP Admin + Super Admin manage everything on this page — mailbox
    assignment and the department export-lock toggle alike. Same two roles
    that already see the sidebar's Administration section this page lives
    in."""
    return bool(user.is_super_admin_user or user.is_erp_admin_user)


def _forbidden(request):
    messages.error(request, 'You do not have access to Email Assigning.')
    return redirect('dashboard:index')


@login_required
def mailbox_list(request):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    mailboxes = ProposalMailbox.objects.select_related('owner', 'assigned_by', 'revoked_by').all()
    form = ProposalMailboxAssignForm()
    context = {'mailboxes': mailboxes, 'form': form}

    # The "Require Email Attachment" export-lock toggle: same access as the
    # rest of the page, ERP Admin + Super Admin.
    if _is_email_admin(request.user):
        existing = {
            row.department: row
            for row in ProposalDepartmentFeature.objects.all()
        }
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


@login_required
@require_POST
def assign_mailbox(request):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    form = ProposalMailboxAssignForm(request.POST)
    if form.is_valid():
        mailbox = form.save(commit=False)
        mailbox.assigned_by = request.user
        mailbox.save()
        messages.success(
            request,
            f'{mailbox.owner.get_full_name() or mailbox.owner.username} can now link '
            f'client emails from {mailbox.email_address} on Technical Proposals.')
    else:
        for field in form:
            for error in field.errors:
                messages.error(request, f'{field.label}: {error}')
    return redirect('email_assignments:list')


@login_required
@require_POST
def toggle_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    mailbox = get_object_or_404(ProposalMailbox, pk=pk)
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


@login_required
@require_POST
def delete_mailbox(request, pk):
    if not _is_email_admin(request.user):
        return _forbidden(request)
    mailbox = get_object_or_404(ProposalMailbox, pk=pk)
    owner = mailbox.owner
    mailbox.delete()
    messages.success(request, f'Removed the mailbox assignment for {owner}.')
    return redirect('email_assignments:list')
