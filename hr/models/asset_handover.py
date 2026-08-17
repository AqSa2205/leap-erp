from django.conf import settings
from django.db import models


class AssetHandover(models.Model):
    """The digital, three-signature asset handover form (LNA_ADM/007), plus
    a two-signature return flow. Applies to either a laptop/equipment Asset
    or a company Vehicle - exactly one of `asset`/`vehicle` is set.

    Issuance signing order: Issued By (HR) -> Authorized By (Super Admin) ->
    Received By (Employee). Only the first Super Admin to actually submit
    the Authorized By signature succeeds - a second attempt after that is
    rejected with a clear "already authorized" message, same race-condition
    guard already used for Attendance Exceptions and Leave Requests.

    Once Received By is signed, the employee becomes the asset/vehicle's
    current holder (mirrored onto AssetAssignment for Assets, so the
    existing "assets in custody" views keep working unchanged).

    Return signing order: Returned By (Employee) -> Received By (HR,
    acknowledging the return). Once acknowledged, custody is released.
    """
    STATUS_CHOICES = [
        ('pending_authorization', 'Pending Authorization'),
        ('pending_receipt', 'Pending Employee Signature'),
        ('active', 'Active / Issued'),
        ('pending_return_confirmation', 'Pending Employee Confirmation'),
        ('returned', 'Returned'),
    ]

    asset = models.ForeignKey(
        'hr.Asset', on_delete=models.CASCADE, null=True, blank=True,
        related_name='handovers')
    vehicle = models.ForeignKey(
        'hr.Vehicle', on_delete=models.CASCADE, null=True, blank=True,
        related_name='handovers')
    employee = models.ForeignKey(
        'hr.Employee', on_delete=models.CASCADE, related_name='asset_handovers')

    accessories = models.TextField(blank=True)
    software_installed = models.TextField(blank=True)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending_authorization')

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='asset_handovers_issued')
    issued_signature = models.ImageField(upload_to='hr/asset_handover_signatures/issued/', null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)

    authorizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='asset_handovers_to_authorize',
        help_text='The Super Admin selected to authorize this handover.')
    authorized_signature = models.ImageField(upload_to='hr/asset_handover_signatures/authorized/', null=True, blank=True)
    authorized_at = models.DateTimeField(null=True, blank=True)

    received_signature = models.ImageField(upload_to='hr/asset_handover_signatures/received/', null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    return_remarks = models.TextField(blank=True)
    returned_signature = models.ImageField(upload_to='hr/asset_handover_signatures/returned/', null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    return_received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='asset_handovers_return_received')
    return_received_signature = models.ImageField(
        upload_to='hr/asset_handover_signatures/return_received/', null=True, blank=True)
    return_received_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'])]

    def __str__(self):
        item = self.asset or self.vehicle
        return f"Handover of {item} to {self.employee.full_name} ({self.status})"

    @property
    def item(self):
        """Whichever of asset/vehicle is actually set - the single 'item'
        this handover concerns, for display code that doesn't care which."""
        return self.asset or self.vehicle


class AssetHandoverAuthorizerPreference(models.Model):
    """Remembers the last-used Authorized By person per HR user, so the
    'set as default authorizer' checkbox actually has something to save
    into. One row per user who has ever checked that box."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='asset_handover_authorizer_preference')
    default_authorizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')

    def __str__(self):
        return f"{self.user}'s default authorizer: {self.default_authorizer}"
