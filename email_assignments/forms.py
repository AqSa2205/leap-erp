from django import forms

from accounts.models import User
from costing.models import RevisionMailbox
from projects.models import MonitoredMailbox
from proposals.models import ProposalMailbox


class _MailboxAssignFormBase(forms.ModelForm):
    """Shared shape for all three per-feature mailbox-assignment forms:
    pick an employee, and the mailbox address is always taken from their
    own User.email — never a free-text field an admin fills in. A free-text
    address let an admin (by typo or by intent) grant an employee read
    access to a completely different person's real mailbox via Graph —
    app-only Mail.Read has no per-mailbox scoping to fall back on, so that
    mismatch was a genuine data-exposure hole, not just a UX footgun.
    Locking this to the employee's own address on file closes it, for all
    three features alike.

    Each subclass sets `owner_related_name` to the OneToOneField's
    related_name on its model (e.g. 'proposal_mailbox'), used to exclude
    employees who already have a row of that specific type — a second
    mailbox for someone is a Revoke + new assignment, not an edit."""

    owner_related_name = None

    class Meta:
        fields = ['owner']
        labels = {'owner': 'Employee'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = User.objects.filter(
            is_active=True, **{f'{self.owner_related_name}__isnull': True}
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
        if owner and self._meta.model.objects.filter(email_address=owner.email).exists():
            raise forms.ValidationError(
                'Another employee already has this exact email address on '
                'file — fix the duplicate under Administration → Users '
                'first.')
        # Taking the address from owner.email closes the free-text hole, but
        # only as far as the User record is trusted. An admin can edit anyone's
        # email under Administration → Users, so pointing a low-privilege
        # account at a colleague's address and then assigning it here would
        # hand that account their inbox — app-only Mail.Read reads whatever
        # address it is given. Refuse when the address belongs to somebody
        # else's account.
        if owner and owner.email:
            clash = (User.objects
                     .filter(email__iexact=owner.email, is_active=True)
                     .exclude(pk=owner.pk)
                     .first())
            if clash:
                raise forms.ValidationError(
                    f'That address is also on {clash.get_full_name() or clash.username}’s '
                    f'account. A mailbox must belong to exactly one person — '
                    f'correct the duplicate under Administration → Users before '
                    f'assigning it.')
        return cleaned_data


class ProposalMailboxAssignForm(_MailboxAssignFormBase):
    """Assign an employee's real mailbox for the Technical Proposal
    'Link Email' feature. See _MailboxAssignFormBase's docstring for why
    the address is derived from the employee's own account rather than
    typed in here."""

    owner_related_name = 'proposal_mailbox'

    class Meta(_MailboxAssignFormBase.Meta):
        model = ProposalMailbox


class RevisionMailboxAssignForm(_MailboxAssignFormBase):
    """Assign an employee's real mailbox for the costing-revision 'send to
    client' / conversation-linking feature. Same pattern as
    ProposalMailboxAssignForm."""

    owner_related_name = 'revision_mailbox'

    class Meta(_MailboxAssignFormBase.Meta):
        model = RevisionMailbox


class MonitoredMailboxAssignForm(_MailboxAssignFormBase):
    """Assign an employee's real mailbox for the Commercial Pipeline
    'Add Emails' feature. Same pattern as ProposalMailboxAssignForm."""

    owner_related_name = 'monitored_mailbox'

    class Meta(_MailboxAssignFormBase.Meta):
        model = MonitoredMailbox
