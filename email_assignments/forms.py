from django import forms

from accounts.models import User
from proposals.models import ProposalMailbox


class ProposalMailboxAssignForm(forms.ModelForm):
    """Assign an employee's real mailbox for the Technical Proposal
    'Link Email' feature. One row per employee (owner is a OneToOne), so the
    employee dropdown only offers people who don't already have one — trying
    to assign a second mailbox to someone is a 'Revoke' + new assignment,
    not an edit, matching how the Django admin screen it replaces behaved."""

    class Meta:
        model = ProposalMailbox
        fields = ['owner', 'email_address']
        labels = {'owner': 'Employee', 'email_address': 'Mailbox address'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = User.objects.filter(
            is_active=True, proposal_mailbox__isnull=True
        ).order_by('first_name', 'last_name', 'username')
        self.fields['owner'].widget.attrs['class'] = 'form-select'
        self.fields['email_address'].widget.attrs.update({
            'class': 'form-control', 'placeholder': 'name@leap-arabia.com',
        })
