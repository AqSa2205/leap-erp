from django.http import HttpResponseForbidden, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import (TemplateView, CreateView, UpdateView,
                                  DeleteView, ListView, DetailView)

from accounts.models import AI_DEVELOPER_ROLE_NAMES, User
from accounts.permissions import CapabilityRequiredMixin, require_capability
from notifications.services import notify_users

from .forms import DevTaskForm, BulkTaskForm, AssignTaskForm
from .models import DevTask, DevTaskUpdate, DevDigest


class DashboardView(CapabilityRequiredMixin, TemplateView):
    capability = 'devtracking.admin'
    template_name = 'devtracking/dashboard.html'

    def get_context_data(self, **kwargs):
        import json
        ctx = super().get_context_data(**kwargs)
        devs = list(User.objects.filter(role__name__in=AI_DEVELOPER_ROLE_NAMES,
                                        is_active=True).order_by('username'))

        # One query for every task; group + derive in Python (small N).
        all_tasks = list(DevTask.objects.select_related('developer'))
        by_dev = {}
        for t in all_tasks:
            by_dev.setdefault(t.developer_id, []).append(t)

        developers = []
        labels, ds_assigned, ds_inprog, ds_done = [], [], [], []
        totals = {'assigned': 0, 'in_progress': 0, 'done': 0, 'blocked': 0,
                  'overdue': 0, 'total': 0}
        for dev in devs:
            tasks = by_dev.get(dev.id, [])
            a = sum(1 for t in tasks if t.status == 'assigned')
            ip = sum(1 for t in tasks if t.status == 'in_progress')
            dn = sum(1 for t in tasks if t.status == 'done')
            bl = sum(1 for t in tasks if t.status == 'blocked')
            ov = sum(1 for t in tasks if t.is_overdue)
            done_on_time = sum(1 for t in tasks if t.status == 'done' and t.on_time is True)
            total = len(tasks)
            developers.append({
                'user': dev, 'assigned': a, 'in_progress': ip, 'done': dn,
                'blocked': bl, 'overdue': ov, 'total': total,
                'pct_done': round(100 * dn / total) if total else 0,
                'on_time_rate': (round(100 * done_on_time / dn) if dn else None),
            })
            labels.append(dev.get_full_name() or dev.username)
            ds_assigned.append(a); ds_inprog.append(ip); ds_done.append(dn)
            for k, v in (('assigned', a), ('in_progress', ip), ('done', dn),
                         ('blocked', bl), ('overdue', ov), ('total', total)):
                totals[k] += v

        # Backlog (unassigned) tasks aren't a developer's responsibility yet,
        # so they don't count toward the per-developer overdue/stuck call-outs.
        overdue_tasks = [t for t in all_tasks if t.developer_id and t.is_overdue]
        stuck_tasks = [t for t in all_tasks if t.developer_id and t.is_stuck]

        ctx.update({
            'developers': developers,
            'team_totals': totals,
            'overdue_tasks': overdue_tasks,
            'stuck_tasks': stuck_tasks,
            'latest_digest': DevDigest.objects.filter(scope='all').first(),
            # Chart.js payloads
            'chart_labels': json.dumps(labels),
            'chart_dev_ids': json.dumps([d['user'].id for d in developers]),
            'chart_assigned': json.dumps(ds_assigned),
            'chart_inprog': json.dumps(ds_inprog),
            'chart_done': json.dumps(ds_done),
            'chart_status': json.dumps([totals['assigned'], totals['in_progress'],
                                        totals['done'], totals['blocked']]),
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


@require_capability('devtracking.admin')
def bulk_create(request):
    """Create a whole list of backlog (unassigned) tasks at once."""
    if request.method == 'POST':
        form = BulkTaskForm(request.POST)
        if form.is_valid():
            priority = form.cleaned_data['priority']
            due = form.cleaned_data.get('due_date')
            DevTask.objects.bulk_create([
                DevTask(title=t, priority=priority, due_date=due,
                        status='unassigned', assigned_by=request.user)
                for t in form.cleaned_data['titles']
            ])
            return redirect('devtracking:backlog')
    else:
        form = BulkTaskForm()
    return render(request, 'devtracking/bulk_create.html', {'form': form})


class BacklogListView(CapabilityRequiredMixin, ListView):
    """Unassigned tasks waiting to be handed to a developer."""
    capability = 'devtracking.admin'
    template_name = 'devtracking/backlog.html'
    context_object_name = 'tasks'
    paginate_by = 50

    def get_queryset(self):
        return DevTask.objects.filter(developer__isnull=True).order_by('-created_at')


@require_capability('devtracking.admin')
def assign_existing(request, pk):
    """Assign a single backlog task to a developer."""
    task = get_object_or_404(DevTask, pk=pk)
    if request.method == 'POST':
        form = AssignTaskForm(request.POST, instance=task)
        if form.is_valid():
            task = form.save(commit=False)
            if task.status == 'unassigned':
                task.status = 'assigned'
            task.assigned_by = request.user
            task.save()
            notify_users(
                recipients=[task.developer],
                verb='assigned you a task',
                actor=request.user,
                description=task.title,
                target_url=reverse('devtracking:my_tasks'),
            )
            return redirect('devtracking:backlog')
    else:
        form = AssignTaskForm(instance=task)
    return render(request, 'devtracking/assign_existing.html', {'form': form, 'task': task})


class TaskEditView(CapabilityRequiredMixin, UpdateView):
    """Edit any task after creation. Gated to devtracking.admin — i.e. only
    AI Head, Admin and Super Admin (the AI doers hold mywork, not admin)."""
    capability = 'devtracking.admin'
    model = DevTask
    form_class = DevTaskForm
    template_name = 'devtracking/task_edit.html'
    success_url = reverse_lazy('devtracking:tasks')

    def form_valid(self, form):
        old_dev_id = DevTask.objects.values_list('developer_id', flat=True).get(pk=self.object.pk)
        response = super().form_valid(form)
        task = self.object
        # Keep status consistent with assignment for non-active tasks only —
        # never disturb an in-progress/blocked/done task's state or timestamps.
        if task.developer_id is None and task.status == 'assigned':
            task.status = 'unassigned'
            task.save(update_fields=['status'])
        elif task.developer_id and task.status == 'unassigned':
            task.status = 'assigned'
            task.save(update_fields=['status'])
        # Notify when the edit (re)assigns the task to a different developer.
        if task.developer_id and task.developer_id != old_dev_id:
            task.assigned_by = self.request.user
            task.save(update_fields=['assigned_by'])
            notify_users(
                recipients=[task.developer],
                verb='assigned you a task',
                actor=self.request.user,
                description=task.title,
                target_url=reverse('devtracking:my_tasks'),
            )
        return response


class TaskDeleteView(CapabilityRequiredMixin, DeleteView):
    """Delete a task. Gated to devtracking.admin (Super Admin, Admin, AI Head)."""
    capability = 'devtracking.admin'
    model = DevTask
    template_name = 'devtracking/task_confirm_delete.html'
    success_url = reverse_lazy('devtracking:tasks')


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
            role__name__in=AI_DEVELOPER_ROLE_NAMES, is_active=True).order_by('username')
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
        tasks = list(DevTask.objects.filter(developer=dev)
                     .select_related('assigned_by'))
        # Refresh live GitHub PR status for tasks that have a PR url. The helper
        # is stale-gated (15 min) + swallows all errors, and each call has a 6s
        # timeout, so a slow/failed GitHub call can never break the page.
        from .github import refresh_if_stale
        for task in tasks:
            if task.github_url:
                refresh_if_stale(task)
        ctx['tasks'] = tasks
        ctx['updates'] = (DevTaskUpdate.objects
                          .filter(task__developer=dev)
                          .select_related('task', 'author')[:20])
        return ctx


class MyTasksView(CapabilityRequiredMixin, ListView):
    capability = 'devtracking.mywork'
    template_name = 'devtracking/my_tasks.html'
    context_object_name = 'tasks'

    def get_queryset(self):
        return (DevTask.objects.filter(developer=self.request.user)
                .select_related('assigned_by'))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tasks = ctx['tasks']
        ctx['active'] = [t for t in tasks if t.status != 'done']
        ctx['done'] = [t for t in tasks if t.status == 'done']
        return ctx


@require_capability('devtracking.admin')
@require_POST
def generate_now(request):
    from .ai import generate_admin_digest
    generate_admin_digest(generated_by=request.user)
    return redirect('devtracking:dashboard')


@require_capability('devtracking.mywork')
@require_POST
def task_action(request, pk):
    task = get_object_or_404(DevTask, pk=pk)
    # Only the task's own developer (or an admin) may act.
    is_admin = request.user.has_capability('devtracking.admin')
    if task.developer_id != request.user.id and not is_admin:
        return HttpResponseForbidden('Not your task')
    action = request.POST.get('action')
    if action == 'start':
        task.mark_started()
    elif action == 'done':
        task.mark_done()
    elif action == 'blocked':
        task.mark_blocked()
    else:
        return HttpResponseBadRequest('Unknown action')
    note = (request.POST.get('note') or '').strip()
    if note or action:
        DevTaskUpdate.objects.create(task=task, author=request.user,
                                     note=note, status_changed_to=task.status)
    return redirect('devtracking:my_tasks')


@require_capability('devtracking.mywork')
@require_POST
def refresh_github(request, pk):
    task = get_object_or_404(DevTask, pk=pk)
    if task.developer_id != request.user.id and not request.user.has_capability('devtracking.admin'):
        return HttpResponseForbidden('Not your task')
    from .github import refresh_task_github
    refresh_task_github(task)
    return redirect(request.META.get('HTTP_REFERER') or 'devtracking:my_tasks')
