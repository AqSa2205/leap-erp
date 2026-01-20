from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.Model):
    """User roles for access control"""
    ADMIN = 'admin'
    MANAGER = 'manager'
    SALES_REP = 'sales_rep'

    ROLE_CHOICES = [
        (ADMIN, 'Administrator'),
        (MANAGER, 'Manager'),
        (SALES_REP, 'Sales Representative'),
    ]

    name = models.CharField(max_length=20, choices=ROLE_CHOICES, unique=True)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.get_name_display()

    @property
    def is_admin(self):
        return self.name == self.ADMIN

    @property
    def is_manager(self):
        return self.name == self.MANAGER

    @property
    def is_sales_rep(self):
        return self.name == self.SALES_REP


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
    def is_admin_user(self):
        return self.role and self.role.is_admin

    @property
    def is_manager_user(self):
        return self.role and self.role.is_manager

    @property
    def is_sales_rep_user(self):
        return self.role and self.role.is_sales_rep

    def can_view_all_projects(self):
        """Admins can view all projects"""
        return self.is_admin_user

    def can_view_region_projects(self):
        """Managers can view projects in their region"""
        return self.is_admin_user or self.is_manager_user

    def can_edit_project(self, project):
        """Check if user can edit a specific project"""
        if self.is_admin_user:
            return True
        if self.is_manager_user and project.region == self.region:
            return True
        if project.owner == self:
            return True
        return False

    def can_delete_project(self):
        """Only admins can delete projects"""
        return self.is_admin_user

    def can_manage_users(self):
        """Only admins can manage users"""
        return self.is_admin_user

    def can_import_excel(self):
        """Admins and managers can import Excel"""
        return self.is_admin_user or self.is_manager_user
