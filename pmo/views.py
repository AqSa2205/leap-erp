"""Project delivery — the overview board and one project's milestone WBS."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from dashboard.views import projects_visible_to
from projects.models import Project

from .models import ONE, ZERO, MilestoneProgressEntry, ProjectMilestone
from .progress import board_rows, leaves, project_completion, validate_weightages


def can_see_delivery(user):
    """Who gets the Project Management department.

    Project Manager and Site Manager are the delivery roles; the rest is the
    usual oversight ladder. Kept as one function so the sidebar, the board and
    the update endpoint cannot disagree about who is allowed in — the workbook
    equivalent was a shared drive with no answer to this at all.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(
        user.is_super_admin_user
        or user.is_admin_user
        or user.is_manager_user
        or user.is_project_manager_user
        or user.is_site_manager_user
    )


def can_update_progress(user):
    """Who may move a completion figure.

    Narrower than viewing on purpose: the whole company's delivery reporting
    is derived from these numbers, so read access and write access are not the
    same decision.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(
        user.is_super_admin_user
        or user.is_admin_user
        or user.is_project_manager_user
        or user.is_site_manager_user
    )


def _visible_projects(user):
    """Delivery projects this user may see, with the milestone tree attached.

    Scoping reuses `dashboard.views.projects_visible_to` rather than growing a
    third copy of the same ladder — that function exists because two copies
    had already drifted apart.
    """
    return (
        projects_visible_to(user)
        .select_related('region', 'status', 'finance')
        .prefetch_related(
            Prefetch('milestones',
                     queryset=ProjectMilestone.objects.prefetch_related('progress_entries')),
            'purchase_orders',
            'finance__milestones',
        )
    )


@login_required
def board(request):
    """Every project on one page, every column derived.

    Replaces the Projects Overview sheet, whose totals summed rows 7–13 while
    the data ran to row 17. There is no range here to get wrong.
    """
    if not can_see_delivery(request.user):
        raise PermissionDenied('The Project Management department is not open to your role.')

    projects = list(_visible_projects(request.user))
    rows = board_rows(projects)
    total_in = sum((r['cash_in'] for r in rows), ZERO)
    total_out = sum((r['cash_out'] for r in rows), ZERO)

    return render(request, 'pmo/board.html', {
        'rows': rows,
        'total_cash_in': total_in,
        'total_cash_out': total_out,
        'total_net': total_in - total_out,
        'problem_count': sum(1 for r in rows if r['weightage_problems']),
        'can_update': can_update_progress(request.user),
    })


@login_required
def project_detail(request, pk):
    """One project's WBS, parents with their children underneath."""
    if not can_see_delivery(request.user):
        raise PermissionDenied('The Project Management department is not open to your role.')

    project = get_object_or_404(_visible_projects(request.user), pk=pk)
    rows = list(project.milestones.all())
    children = {}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row)

    # Flattened for the template: a parent immediately followed by its own
    # children, so the grid is one <tbody> and keyboard navigation runs down it
    # in the order somebody reads.
    #
    # Weights are summed here rather than read from the model properties. Those
    # walk `self.children` per row, which is a query each — fine for one row on
    # a page, but this page is the whole tree.
    display = []
    for parent in sorted((r for r in rows if r.parent_id is None), key=lambda r: r.order):
        kids = sorted(children.get(parent.pk, []), key=lambda r: r.order)
        display.append({
            'row': parent,
            'is_parent': True,
            'number': str(parent.order),
            'weightage': sum((k.weightage for k in kids), ZERO) if kids else parent.weightage,
            'completed_weightage': sum(
                (k.weightage * k.completed_fraction for k in kids), ZERO),
        })
        for child in kids:
            display.append({
                'row': child,
                'is_parent': False,
                'number': f'{parent.order}.{child.order}',
                'weightage': child.weightage,
                'completed_weightage': child.weightage * child.completed_fraction,
            })

    completion = project_completion(project)
    return render(request, 'pmo/project_detail.html', {
        'project': project,
        'display': display,
        'completion': completion,
        'completion_pct': completion * Decimal('100'),
        'problems': validate_weightages(project),
        'leaf_count': len(leaves(project)),
        'can_update': can_update_progress(request.user),
    })


@require_POST
@login_required
def update_progress(request, pk):
    """Set one milestone's completed fraction. Called per cell by the grid.

    Every change is appended to the progress log as well as written to the
    row, which is what makes "when was this last updated" answerable — the
    workbook's version of that column was TODAY() and always read as today.
    """
    if not can_update_progress(request.user):
        return JsonResponse({'error': 'You cannot update delivery progress.'}, status=403)

    milestone = get_object_or_404(
        ProjectMilestone.objects.select_related('project'), pk=pk)
    if not _visible_projects(request.user).filter(pk=milestone.project_id).exists():
        return JsonResponse({'error': 'Project not found.'}, status=404)

    # Progress belongs to the activities that carry weight. Accepting it on a
    # summary row would let a parent and its children both claim the same
    # weight, and the project would read as more complete than it is.
    if milestone.children.exists():
        return JsonResponse(
            {'error': 'This is a summary row — update the activities under it.'},
            status=400)

    raw = (request.POST.get('completed_fraction') or '').strip()
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return JsonResponse({'error': f'"{raw}" is not a number.'}, status=400)
    if value < ZERO or value > ONE:
        return JsonResponse(
            {'error': 'Progress is a fraction between 0 and 1.'}, status=400)

    milestone.completed_fraction = value
    milestone.save(update_fields=['completed_fraction', 'updated_at'])
    MilestoneProgressEntry.objects.create(
        milestone=milestone, completed_fraction=value,
        note=(request.POST.get('note') or '').strip(),
        recorded_by=request.user)

    project = milestone.project
    return JsonResponse({
        'ok': True,
        'completed_fraction': str(value),
        'completed_weightage': str(milestone.weightage * value),
        'project_completion_pct': str(project_completion(project) * Decimal('100')),
    })
