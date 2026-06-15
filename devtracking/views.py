from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView, CreateView, ListView, DetailView

from accounts.models import Role, User
from accounts.permissions import CapabilityRequiredMixin
from notifications.services import notify_users

from .forms import DevTaskForm
from .models import DevTask, DevTaskUpdate, DevDigest


class DashboardView(CapabilityRequiredMixin, TemplateView):
    capability = 'devtracking.admin'
    template_name = 'devtracking/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.now().date()
        devs = User.objects.filter(role__name=Role.DEVELOPER, is_active=True).order_by('username')

        developers = []
        for dev in devs:
            tasks = DevTask.objects.filter(developer=dev)
            developers.append({
                'user': dev,
                'assigned': tasks.filter(status='assigned').count(),
                'in_progress': tasks.filter(status='in_progress').count(),
                'done': tasks.filter(status='done').count(),
                'overdue': tasks.exclude(status='done').filter(
                    due_date__lt=today, due_date__isnull=False).count(),
            })

        overdue_tasks = (DevTask.objects.select_related('developer')
                         .exclude(status='done')
                         .filter(due_date__lt=today, due_date__isnull=False))
        # is_stuck = in_progress >= 3 days; compute in Python via the property.
        stuck_tasks = [t for t in DevTask.objects.select_related('developer')
                       .filter(status='in_progress') if t.is_stuck]

        ctx.update({
            'developers': developers,
            'overdue_tasks': overdue_tasks,
            'stuck_tasks': stuck_tasks,
            'latest_digest': DevDigest.objects.filter(scope='all').first(),
        })
        return ctx


class TaskAssignView(CapabilityRequiredMixin, CreateView):
    capability = 'devtracking.admin'
    form_class = DevTaskForm
    template_name = 'devtracking/task_form.html'
    success_url = reverse_lazy('devtracking:tasks')

    def form_valid(self, form):
        form.instance.assigned_by = self.request.user
        response = super().form_valid(form)
        notify_users(
            recipients=[self.object.developer],
            verb='assigned you a task',
            actor=self.request.user,
            description=self.object.title,
            target_url=reverse('devtracking:my_tasks'),
        )
        return response


class TaskListView(CapabilityRequiredMixin, ListView):
    capability = 'devtracking.admin'
    template_name = 'devtracking/task_list.html'
    context_object_name = 'tasks'
    paginate_by = 30

    def get_queryset(self):
        qs = DevTask.objects.select_related('developer', 'assigned_by')
        dev = self.request.GET.get('developer')
        status = self.request.GET.get('status')
        if dev:
            qs = qs.filter(developer_id=dev)
        if status:
            qs = qs.filter(status=status)
        if self.request.GET.get('overdue') == '1':
            today = timezone.now().date()
            qs = qs.exclude(status='done').filter(due_date__lt=today, due_date__isnull=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['developers'] = User.objects.filter(
            role__name=Role.DEVELOPER, is_active=True).order_by('username')
        ctx['status_choices'] = DevTask.STATUS_CHOICES
        ctx['cur_developer'] = self.request.GET.get('developer', '')
        ctx['cur_status'] = self.request.GET.get('status', '')
        ctx['cur_overdue'] = self.request.GET.get('overdue', '')
        return ctx


class DevDetailView(CapabilityRequiredMixin, DetailView):
    capability = 'devtracking.admin'
    model = User
    template_name = 'devtracking/dev_detail.html'
    context_object_name = 'developer'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        dev = self.object
        ctx['tasks'] = (DevTask.objects.filter(developer=dev)
                        .select_related('assigned_by'))
        ctx['updates'] = (DevTaskUpdate.objects
                          .filter(task__developer=dev)
                          .select_related('task', 'author')[:20])
        return ctx


# Task-4 stub: leave intact so nav `{% url %}` resolves.
@login_required
def my_tasks_stub(request):
    return render(request, 'devtracking/coming_soon.html', {'title': 'My Tasks'})
