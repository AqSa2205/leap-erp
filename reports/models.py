from django.db import models
from django.conf import settings


class Vendor(models.Model):
    """Partner, Distributor & Vendor companies"""
    VENDOR_TYPE_CHOICES = [
        ('vendor', 'Vendor'),
        ('distributor', 'Distributor'),
        ('partner', 'Partner'),
        ('oem', 'OEM'),
    ]

    name = models.CharField(max_length=255)
    vendor_type = models.CharField(max_length=20, choices=VENDOR_TYPE_CHOICES, default='vendor')
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    products_services = models.TextField(blank=True, help_text="Products or services provided")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_vendor_type_display()})"


class EPC(models.Model):
    """EPC Contractors"""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    region = models.CharField(max_length=100, blank=True, help_text="Operating regions")
    specialization = models.TextField(blank=True, help_text="Areas of specialization")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'EPC Contractor'
        verbose_name_plural = 'EPC Contractors'

    def __str__(self):
        return self.name


class Exhibition(models.Model):
    """Exhibitions and Trade Shows attended"""
    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    year = models.CharField(max_length=4, blank=True)
    description = models.TextField(blank=True)
    website = models.URLField(max_length=500, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    leads_generated = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    is_attended = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-year', 'name']

    def __str__(self):
        return f"{self.name} ({self.year})"


class ProcurementPortal(models.Model):
    """Procurement portals and registrations"""
    REGISTRATION_TYPE_CHOICES = [
        ('free', 'Free Registration'),
        ('freemium', 'Freemium Registration'),
        ('paid', 'Paid Registration'),
    ]

    name = models.CharField(max_length=255)
    registration_type = models.CharField(max_length=20, choices=REGISTRATION_TYPE_CHOICES, default='free')
    website = models.URLField(max_length=500, blank=True)
    username = models.CharField(max_length=255, blank=True)
    registration_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    annual_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_registration_type_display()})"


class Certification(models.Model):
    """Company certifications and accreditations"""
    STATUS_CHOICES = [
        ('obtained', 'Obtained'),
        ('in_progress', 'In Progress'),
        ('pending', 'Pending'),
        ('expired', 'Expired'),
    ]

    name = models.CharField(max_length=255)
    issuing_body = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    certificate_number = models.CharField(max_length=100, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class SalesContact(models.Model):
    """Sales call contacts tracking"""
    CATEGORY_CHOICES = [
        ('oil_gas', 'Oil & Gas'),
        ('energy', 'Energy'),
        ('construction', 'Construction'),
        ('manufacturing', 'Manufacturing'),
        ('government', 'Government'),
        ('other', 'Other'),
    ]

    company_name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='other')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_contacted = models.BooleanField(default=False)
    last_contact_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company_name']

    def __str__(self):
        return f"{self.company_name} - {self.contact_name}"


class SalesCallReport(models.Model):
    """Daily Sales Call Reports entered by Sales Representatives"""

    # Action Type choices
    ACTION_TYPE_CHOICES = [
        ('email_call', 'Email/Call'),
        ('meeting', 'Meeting'),
        ('site_visit', 'Site Visit'),
        ('presentation', 'Presentation'),
        ('follow_up', 'Follow Up'),
        ('proposal_sent', 'Proposal Sent'),
        ('negotiation', 'Negotiation'),
        ('other', 'Other'),
    ]

    # Contact Information/Type choices
    CONTACT_TYPE_CHOICES = [
        ('direct', 'Direct'),
        ('referral', 'Referral'),
        ('new_customer', 'New Customer'),
        ('existing_customer', 'Existing Customer'),
        ('cold_call', 'Cold Call'),
        ('inbound', 'Inbound Inquiry'),
        ('trade_show', 'Trade Show Lead'),
        ('online', 'Online Lead'),
        ('other', 'Other'),
    ]

    # Systems/Category choices (based on Excel tabs)
    SYSTEM_CATEGORY_CHOICES = [
        ('cctv', 'CCTV'),
        ('radios', 'Radios'),
        ('acs', 'Access Control Systems (ACS)'),
        ('iot', 'IoT'),
        ('iiot', 'Industrial IoT (IIoT)'),
        ('servers', 'Servers'),
        ('network_security', 'Network & Security'),
        ('firewall', 'Firewall'),
        ('cyber_security', 'Cyber Security'),
        ('windows', 'Windows'),
        ('ot', 'Operational Technology (OT)'),
        ('solar', 'Solar'),
        ('energy', 'Energy'),
        ('council', 'Council'),
        ('education', 'Education'),
        ('healthcare', 'Healthcare'),
        ('manufacturing', 'Manufacturing'),
        ('oil_gas', 'Oil & Gas'),
        ('other', 'Other'),
    ]

    # Title choices
    TITLE_CHOICES = [
        ('mr', 'Mr'),
        ('mrs', 'Mrs'),
        ('ms', 'Ms'),
        ('dr', 'Dr'),
        ('prof', 'Prof'),
        ('other', 'Other'),
    ]

    # Goal choices
    GOAL_CHOICES = [
        ('company_intro', 'Company Introduction'),
        ('product_demo', 'Product Demo'),
        ('requirement_gathering', 'Requirement Gathering'),
        ('proposal_discussion', 'Proposal Discussion'),
        ('pricing_negotiation', 'Pricing Negotiation'),
        ('contract_signing', 'Contract Signing'),
        ('follow_up', 'Follow Up'),
        ('relationship_building', 'Relationship Building'),
        ('technical_discussion', 'Technical Discussion'),
        ('complaint_resolution', 'Complaint Resolution'),
        ('other', 'Other'),
    ]

    # Next Action Type choices
    NEXT_ACTION_CHOICES = [
        ('send_email', 'Send Email'),
        ('make_call', 'Make Call'),
        ('schedule_meeting', 'Schedule Meeting'),
        ('send_proposal', 'Send Proposal'),
        ('follow_up', 'Follow Up'),
        ('site_visit', 'Site Visit'),
        ('demo_scheduled', 'Demo Scheduled'),
        ('waiting_response', 'Waiting for Response'),
        ('meeting_done', 'Meeting Done/Feedback Received'),
        ('closed_won', 'Closed - Won'),
        ('closed_lost', 'Closed - Lost'),
        ('on_hold', 'On Hold'),
        ('other', 'Other'),
    ]

    # Core fields
    call_date = models.DateField(verbose_name="Call Date")
    action_type = models.CharField(max_length=30, choices=ACTION_TYPE_CHOICES, default='email_call', verbose_name="Action Type")
    contact_type = models.CharField(max_length=30, choices=CONTACT_TYPE_CHOICES, default='direct', verbose_name="Contact Information/Type")
    system_categories = models.TextField(default='other', verbose_name="Systems Relates", help_text="Comma-separated category codes")

    # Company Information
    company_name = models.CharField(max_length=255, verbose_name="Company")

    # Contact Person Details
    contact_name = models.CharField(max_length=255, verbose_name="Contact Name")
    title = models.CharField(max_length=10, choices=TITLE_CHOICES, blank=True, verbose_name="Title")
    role = models.CharField(max_length=255, blank=True, verbose_name="Role/Position")
    address = models.TextField(blank=True, verbose_name="Address")
    phone = models.CharField(max_length=100, blank=True, verbose_name="Phone")
    email = models.EmailField(blank=True, verbose_name="Email")

    # Call Details
    goal = models.CharField(max_length=30, choices=GOAL_CHOICES, default='company_intro', verbose_name="Goal")
    comments = models.TextField(blank=True, verbose_name="Comments/Notes")

    # Scheduled Next Action
    next_action_date = models.DateField(null=True, blank=True, verbose_name="Next Action Date")
    next_action_type = models.CharField(max_length=30, choices=NEXT_ACTION_CHOICES, blank=True, verbose_name="Next Action Type")

    # Tracking
    sales_rep = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sales_call_reports',
        verbose_name="Sales Representative"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-call_date', '-created_at']
        verbose_name = 'Sales Call Report'
        verbose_name_plural = 'Sales Call Reports'

    def __str__(self):
        return f"{self.call_date} - {self.company_name} ({self.sales_rep})"

    def get_system_categories_list(self):
        """Return list of selected category codes."""
        if self.system_categories:
            return [cat.strip() for cat in self.system_categories.split(',') if cat.strip()]
        return []

    def get_system_categories_display(self):
        """Return display names for selected categories."""
        category_dict = dict(self.SYSTEM_CATEGORY_CHOICES)
        selected = self.get_system_categories_list()
        return [category_dict.get(cat, cat) for cat in selected]


class SalesCallResponse(models.Model):
    """Manager/Admin responses to sales call reports"""
    sales_call = models.ForeignKey(
        SalesCallReport,
        on_delete=models.CASCADE,
        related_name='responses'
    )
    responder = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='sales_call_responses'
    )
    message = models.TextField(help_text='Response message from manager/admin')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Response by {self.responder} on {self.created_at.strftime('%Y-%m-%d')}"
