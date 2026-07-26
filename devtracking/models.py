from django.db import models
from django.conf import settings
from django.utils import timezone


class TaskStack(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='dev_stacks_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class DevTask(models.Model):
    PRIORITY_CHOICES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]
    STATUS_CHOICES = [('unassigned', 'Unassigned'), ('assigned', 'Assigned'),
                      ('in_progress', 'In progress'), ('blocked', 'Blocked'),
                      ('done', 'Done')]

    # Nullable: a task can sit in the backlog (unassigned) before it's given to
    # a developer. Assigning sets the developer + flips status to 'assigned'.
    developer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='dev_tasks', null=True, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='dev_tasks_assigned')
    stack = models.ForeignKey('TaskStack', on_delete=models.SET_NULL, null=True,
                              blank=True, related_name='tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='unassigned')
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    github_url = models.URLField(blank=True)
    gh_state = models.CharField(max_length=10, blank=True)
    gh_commits = models.PositiveIntegerField(null=True, blank=True)
    gh_title = models.CharField(max_length=300, blank=True)
    gh_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} -> {self.developer or "unassigned"}'

    def save(self, *args, **kwargs):
        if self.developer_id and self.status not in ('in_progress', 'blocked', 'done'):
            self.status = 'assigned'
            if self.assigned_at is None:
                self.assigned_at = timezone.now()
        elif not self.developer_id and self.status == 'assigned':
            self.status = 'unassigned'
            self.assigned_at = None
        super().save(*args, **kwargs)

    @property
    def is_unassigned(self):

        return self.developer_id is None

    def assign_to(self, developer, by=None):
        """Give a backlog task to a developer (or reassign). Flips an
        unassigned task to 'assigned'; never disturbs in-progress/done state."""
        self.developer = developer
        if by is not None:
            self.assigned_by = by
        if self.status == 'unassigned':
            self.status = 'assigned'
        self.save()

    def mark_started(self):
        if self.started_at is None:
            self.started_at = timezone.now()
        if self.status in ('assigned', 'blocked'):
            self.status = 'in_progress'
        self.save()

    def mark_done(self):
        if self.started_at is None:
            self.started_at = timezone.now()
        self.completed_at = timezone.now()
        self.status = 'done'
        self.save()

    def mark_blocked(self):
        self.status = 'blocked'
        self.save()

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status != 'done'
                    and self.due_date < timezone.now().date())

    @property
    def on_time(self):
        if self.status != 'done' or not self.completed_at or not self.due_date:
            return None
        return self.completed_at.date() <= self.due_date

    @property
    def elapsed(self):
        if not self.started_at:
            return None
        end = self.completed_at or timezone.now()
        return end - self.started_at

    @property
    def is_stuck(self):
        if self.status != 'in_progress' or not self.started_at:
            return False
        return (timezone.now() - self.started_at).days >= 3


class DevTaskUpdate(models.Model):
    task = models.ForeignKey(DevTask, on_delete=models.CASCADE, related_name='updates')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    note = models.TextField(blank=True)
    status_changed_to = models.CharField(max_length=12, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class DevDigest(models.Model):
    period_date = models.DateField()
    scope = models.CharField(max_length=20, default='all')
    content = models.TextField()
    model_used = models.CharField(max_length=60, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-generated_at']
