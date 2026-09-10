from django import forms

from accounts.models import User
from proposals.models import ProposalMailbox


class ProposalMailboxAssignForm(forms.ModelForm):
    """Assign an employee's real mailbox for the Technical Proposal
    'Link Email' feature. One row per employee (owner is a OneToOne), so the
    employee dropdown only offers people who don't already have one — trying
    to assign a second mailbox to someone is a 'Revoke' + new assignment,
    not an edit, matching how the Django admin screen it replaces behaved.

    The mailbox address is NOT a free-text field here on purpose: it's
    always taken from the employee's own User.email, never typed in by the
    admin. A free-text address let an admin (by typo or by intent) grant an
    employee read access to a completely different person's real mailbox
    via Graph — app-only Mail.Read has no per-mailbox scoping to fall back
    on, so that mismatch was a genuine data-exposure hole, not just a UX
    footgun. Locking this to the employee's own address on file closes it."""

    class Meta:
        model = ProposalMailbox
        fields = ['owner']
        labels = {'owner': 'Employee'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = User.objects.filter(
            is_active=True, proposal_mailbox__isnull=True
        ).order_by('first_name', 'last_name', 'username')
        self.fields['owner'].widget.attrs['class'] = 'form-select'

    def clean(self):
        cleaned_data = super().clean()
        owner = cleaned_data.get('owner')
        if owner and not owner.email:
            raise forms.ValidationError(
                'This employee has no email address on file — add one to '
                'their account first (Administration → Users) before '
                'assigning a mailbox.')
        if owner and ProposalMailbox.objects.filter(email_address=owner.email).exists():
            raise forms.ValidationError(
                'Another employee already has this exact email address on '
                'file — fix the duplicate under Administration → Users '
                'first.')
        return cleaned_data
