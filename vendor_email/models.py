from django.conf import settings
from django.db import models


class VendorEmailMessage(models.Model):
    """One inbound message read from the monitored mailbox.

    Every message we read gets a row here — matched or not — so the queue
    is a full audit trail, not just the failures. `message_id` is unique so
    re-checking the same mailbox never creates duplicates.
    """

    STATUS_CHOICES = [
        ('matched_auto', 'Auto-attached'),
        ('unmatched', 'Needs review'),
        ('resolved_manual', 'Resolved manually'),
    ]

    message_id = models.CharField(max_length=255, unique=True)
    received_at = models.DateTimeField()
    sender_email = models.EmailField()
    sender_name = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=500)
    body_preview = models.TextField(blank=True)

    extracted_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='unmatched')
    match_reason = models.CharField(
        max_length=255, blank=True,
        help_text='Why the matcher matched or skipped this message.',
    )

    matched_sheet = models.ForeignKey(
        'costing.CostingSheet', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='matched_emails',
    )
    matched_vendor_quote = models.ForeignKey(
        'costing.VendorQuote', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='source_email',
    )

    attachment_filename = models.CharField(max_length=255, blank=True)
    attachment_file = models.FileField(upload_to='vendor_email/attachments/', blank=True, null=True)

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vendor_emails_resolved',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f'{self.sender_email} — {self.subject}'