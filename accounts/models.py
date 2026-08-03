from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.Model):
    """User roles for access control"""
    SUPER_ADMIN = 'super_admin'
    ADMIN = 'admin'
    MANAGER = 'manager'
    SALES_REP = 'sales_rep'
    PROCUREMENT_MGR = 'procurement_mgr'
    PROCUREMENT_OFF = 'procurement_off'
    PROPOSAL_HEAD = 'proposal_head'
    PROPOSAL_REP = 'proposal_rep'
    FINANCE_HEAD = 'finance_head'
    FINANCE_MANAGER = 'finance_manager'
    FINANCE_REP = 'finance_rep'
    DEVELOPER = 'developer'
    AI_HEAD = 'ai_head'
    AI_INTERN = 'ai_intern'
    AI_ENGINEER = 'ai_engineer'
    AI_JUNIOR_ENGINEER = 'ai_junior_engineer'
    # HR/administration authorization roles. PROJECT_MANAGER, SITE_MANAGER and
    # DOCUMENT_CONTROLLER are team-scoped (they see only their own org-chart
    # reports); ERP_ADMIN gets the whole Administration section, company-wide,
    # minus the super-admin-only tools (user mgmt, permission matrix, password
    # reset, storage). See hr/scoping.py for how the scope is resolved.
    PROJECT_MANAGER = 'project_manager'
    SITE_MANAGER = 'site_manager'
    ERP_ADMIN = 'erp_admin'
    DOCUMENT_CONTROLLER = 'document_controller'

    ROLE_CHOICES = [
        (SUPER_ADMIN, 'Super Administrator'),
        (ADMIN, 'Administrator'),
        (MANAGER, 'Manager'),
        (SALES_REP, 'Sales Representative'),
        (PROCUREMENT_MGR, 'Procurement Manager'),
        (PROCUREMENT_OFF, 'Procurement Officer'),
        (PROPOSAL_HEAD, 'Proposal Team Head'),
        (PROPOSAL_REP, 'Proposal Representative'),
        (FINANCE_HEAD, 'Finance Head'),
        (FINANCE_MANAGER, 'Finance Manager'),
        (FINANCE_REP, 'Finance Representative'),
        (DEVELOPER, 'Developer'),
        (AI_HEAD, 'AI Head'),
        (AI_INTERN, 'AI Intern'),
        (AI_ENGINEER, 'AI Engineer'),
        (AI_JUNIOR_ENGINEER, 'Junior AI Engineer'),
        (PROJECT_MANAGER, 'Project Manager'),
        (SITE_MANAGER, 'Site Manager'),
        (ERP_ADMIN, 'ERP Admin'),
        (DOCUMENT_CONTROLLER, 'Document Controller'),
    ]

    name = models.CharField(max_length=20, choices=ROLE_CHOICES, unique=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.get_name_display()

    @property
    def is_super_admin(self):
        return self.name == self.SUPER_ADMIN

    @property
    def is_admin(self):
        return self.name == self.ADMIN

    @property
    def is_manager(self):
        return self.name == self.MANAGER

    @property
    def is_sales_rep(self):
        return self.name == self.SALES_REP

    @property
    def is_procurement_manager(self):
        return self.name == self.PROCUREMENT_MGR

    @property
    def is_procurement_officer(self):
        return self.name == self.PROCUREMENT_OFF

    @property
    def is_proposal_head(self):
        return self.name == self.PROPOSAL_HEAD

    @property
    def is_proposal_rep(self):
        return self.name == self.PROPOSAL_REP

    @property
    def is_finance_head(self):
        return self.name == self.FINANCE_HEAD

    @property
    def is_finance_manager(self):
        return self.name == self.FINANCE_MANAGER

    @property
    def is_finance_rep(self):
        return self.name == self.FINANCE_REP

    @property
    def is_developer(self):
        return self.name == self.DEVELOPER

    @property
    def is_ai_head(self):
        return self.name == self.AI_HEAD

    @property
    def is_project_manager(self):
        return self.name == self.PROJECT_MANAGER

    @property
    def is_site_manager(self):
        return self.name == self.SITE_MANAGER

    @property
    def is_erp_admin(self):
        return self.name == self.ERP_ADMIN

    @property
    def is_document_controller(self):
        return self.name == self.DOCUMENT_CONTROLLER


# Roles whose users are tracked as "developers" in the devtracking module
# (assignable to tasks, appear in the AI digest). AI Head manages, so is NOT here.
AI_DEVELOPER_ROLE_NAMES = (
    Role.DEVELOPER, Role.AI_INTERN, Role.AI_ENGINEER, Role.AI_JUNIOR_ENGINEER,
)


class User(AbstractUser):
    """Custom user model with role and region"""
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users'
    )
    region = models.ForeignKey(
        'projects.Region',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users'
    )
    phone = models.CharField(max_length=20, blank=True)
    employee_code = models.CharField(max_length=20, blank=True, unique=True, null=True)

    class Meta:
        ordering = ['username']

    def __str__(self):
        return f"{self.get_full_name() or self.username}"

    @property
    def is_super_admin_user(self):
        return self.role and self.role.is_super_admin

    @property
    def is_admin_user(self):
        return self.role and self.role.is_admin

    @property
    def is_manager_user(self):
        return self.role and self.role.is_manager

    @property
    def is_sales_rep_user(self):
        return self.role and self.role.is_sales_rep

    @property
    def is_procurement_manager_user(self):
        return self.role and self.role.is_procurement_manager

    @property
    def is_procurement_officer_user(self):
        return self.role and self.role.is_procurement_officer

    @property
    def is_procurement_user(self):
        """Either procurement manager or officer."""
        return self.is_procurement_manager_user or self.is_procurement_officer_user

    @property
    def is_proposal_head_user(self):
        return self.role and self.role.is_proposal_head

    @property
    def is_proposal_rep_user(self):
        return self.role and self.role.is_proposal_rep

    @property
    def is_proposal_team_user(self):
        """Either proposal team head or representative.
        Used to decide if a user should see BOM view vs full costing."""
        return self.is_proposal_head_user or self.is_proposal_rep_user

    @property
    def is_finance_head_user(self):
        return self.role and self.role.is_finance_head

    @property
    def is_finance_manager_user(self):
        return self.role and self.role.is_finance_manager

    @property
    def is_finance_rep_user(self):
        return self.role and self.role.is_finance_rep

    @property
    def is_finance_team_user(self):
        """Any finance role — head, manager, or rep. Used to gate access
        to the Finance module (which is restricted to this team)."""
        return (
            self.is_finance_head_user
            or self.is_finance_manager_user
            or self.is_finance_rep_user
        )

    @property
    def is_developer_user(self):
        return bool(self.role and self.role.is_developer)

    @property
    def is_ai_head_user(self):
        return bool(self.role and self.role.is_ai_head)

    @property
    def is_ai_developer_user(self):
        return bool(self.role and self.role.name in AI_DEVELOPER_ROLE_NAMES)

    @property
    def is_ai_team_user(self):
        """The whole AI department — head + developers/engineers/interns."""
        return self.is_ai_head_user or self.is_ai_developer_user

    @property
    def is_project_manager_user(self):
        return bool(self.role and self.role.is_project_manager)

    @property
    def is_site_manager_user(self):
        return bool(self.role and self.role.is_site_manager)

    @property
    def is_erp_admin_user(self):
        return bool(self.role and self.role.is_erp_admin)

    @property
    def is_document_controller_user(self):
        return bool(self.role and self.role.is_document_controller)

    @property
    def is_team_scoped_hr_user(self):
        """The team-scoped HR roles — Project Manager, Site Manager, and
        Document Controller. These get the HR feature set (attendance, leave,
        assets, team exceptions, org chart, KPIs) restricted to their own
        org-chart reports (see hr/scoping.py). Distinct from ERP Admin, which
        gets the same features but company-wide."""
        return (
            self.is_project_manager_user
            or self.is_site_manager_user
            or self.is_document_controller_user
        )

    @property
    def is_sales_team_user(self):
        """Sales side: reps, managers, admins, super admins.
        These users see the full costing sheet (including pricing)."""
        return (
            self.is_sales_rep_user or self.is_manager_user or
            self.is_admin_user or self.is_super_admin_user
        )

    def can_view_all_projects(self):
        """Super admins can view all projects"""
        return self.is_super_admin_user

    def can_view_region_projects(self):
        """Super admins, admins, and managers can view projects in their region"""
        return self.is_super_admin_user or self.is_admin_user or self.is_manager_user

    def can_edit_project(self, project):
        """Check if user can edit a specific project"""
        if self.is_super_admin_user:
            return True
        if self.is_admin_user and project.region == self.region:
            return True
        if self.is_manager_user and project.region == self.region:
            return True
        if project.owner == self:
            return True
        return False

    def has_capability(self, codename):
        """Single enforcement API. super_admin always passes (lockout safety).

        Caches the role's allowed codenames on the instance so nav rendering
        does not fan out queries. Cache lives for the life of this Python
        object (i.e. one request via `request.user`).
        """
        if not self.is_authenticated:
            return False
        if self.is_super_admin_user:
            return True
        if self.role_id is None:
            return False
        cache = getattr(self, '_capability_cache', None)
        if cache is None:
            cache = set(
                RolePermission.objects
                .filter(role_id=self.role_id, allowed=True)
                .values_list('codename', flat=True)
            )
            self._capability_cache = cache
        return codename in cache

    def can_delete_project(self):
        """Super admins and admins can delete projects"""
        return self.is_super_admin_user or self.is_admin_user

    def can_manage_users(self):
        """Only super admins can manage users"""
        return self.is_super_admin_user

    def can_import_excel(self):
        """Super admins, admins, and managers can import Excel"""
        return self.is_super_admin_user or self.is_admin_user or self.is_manager_user


class PasswordResetRequest(models.Model):
    """Password reset request — generated by super admin, approved after user sets new password."""

    STATUS_CHOICES = [
        ('pending_user', 'Waiting for User'),
        ('approved', 'Completed'),
        ('rejected', 'Cancelled'),
        ('expired', 'Expired'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_requests')
    token = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending_user')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='sent_reset_requests')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Reset for {self.user.username} - {self.get_status_display()}"

    @property
    def is_expired(self):
        from django.utils import timezone
        from datetime import timedelta
        return self.created_at < timezone.now() - timedelta(days=7)


class RolePermission(models.Model):
    """Per-role on/off grant for a capability codename (see accounts.permissions)."""
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='permissions')
    codename = models.CharField(max_length=64)
    allowed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # unique_together already creates the backing index on (role, codename),
        # so no separate models.Index is needed.
        unique_together = ('role', 'codename')

    def __str__(self):
        return f"{self.role.name}:{self.codename}={'on' if self.allowed else 'off'}"


class PermissionChangeLog(models.Model):
    """Audit trail for grant toggles (security-sensitive)."""
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name='+')
    codename = models.CharField(max_length=64)
    allowed = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        state = 'on' if self.allowed else 'off'
        return f"{self.actor} set {self.role.name}:{self.codename}={state} @ {self.created_at:%Y-%m-%d %H:%M}"
