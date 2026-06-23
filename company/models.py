from django.conf import settings
from django.db import models


class CompanyDocument(models.Model):
    """Company-wide important documents: certifications, registrations,
    licenses, ISO certificates, legal/financial documents, etc."""

    DOC_TYPE_CHOICES = [
        ('certification', 'Certification'),
        ('registration', 'Registration'),
        ('license', 'License / Permit'),
        ('iso', 'ISO Certificate'),
        ('legal', 'Legal Document'),
        ('financial', 'Financial Document'),
        ('insurance', 'Insurance'),
        ('other', 'Other'),
    ]

    title = models.CharField(max_length=255)
    document_type = models.CharField(
        max_length=30, choices=DOC_TYPE_CHOICES, default='other')
    custom_type = models.CharField(
        max_length=100, blank=True,
        help_text="Label used when the type is 'Other'.")
    file = models.FileField(upload_to='company_documents/%Y/%m/')
    issuing_authority = models.CharField(max_length=255, blank=True)
    reference_number = models.CharField(
        max_length=100, blank=True,
        help_text="Certificate / registration / license number.")
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='uploaded_company_docs')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Company Document'
        verbose_name_plural = 'Company Documents'

    def __str__(self):
        return f"{self.title} ({self.type_label})"

    @property
    def type_label(self):
        if self.document_type == 'other' and self.custom_type:
            return self.custom_type
        return self.get_document_type_display()

    @property
    def expiry_status(self):
        """'expired' / 'expiring_soon' (<=30 days) / 'valid', or None if no expiry."""
        if not self.expiry_date:
            return None
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.now().date()
        if self.expiry_date < today:
            return 'expired'
        if self.expiry_date <= today + timedelta(days=30):
            return 'expiring_soon'
        return 'valid'

    @property
    def filename(self):
        import os
        return os.path.basename(self.file.name) if self.file else ''

    @property
    def file_extension(self):
        import os
        _, ext = os.path.splitext(self.file.name) if self.file else ('', '')
        return ext.lower()

    @property
    def is_image(self):
        return self.file_extension in ('.jpg', '.jpeg', '.png', '.gif', '.webp')

    @property
    def is_pdf(self):
        return self.file_extension == '.pdf'
