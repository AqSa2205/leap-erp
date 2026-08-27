"""Everything waiting on one person's decision, gathered in one place.

The point of the Pending Approvals tab is that nobody should have to walk the
system looking for work assigned to them. The risk in gathering it is that
this file becomes a second opinion about who may decide what — so every source
below reuses the same predicate the real page uses. If a row shows up here,
the page it links to will let that user act on it; if it does not show up
here, the page would have refused them anyway.

Adding a source: append to SOURCES. Each callable takes the user and returns
a group dict, or None when that source does not apply to them.
"""


def _group(key, label, icon, url, items, empty_reason=''):
    return {
        'key': key,
        'label': label,
        'icon': icon,
        'url': url,
        'items': items,
        'count': len(items),
        'empty_reason': empty_reason,
    }


def _item(title, subtitle, when, url, meta=''):
    return {'title': title, 'subtitle': subtitle, 'when': when, 'url': url,
            'meta': meta}


# ── Leave ────────────────────────────────────────────────────────────────────

def leave_requests(user):
    """Leave waiting on this user's own signature.

    Keyed off LeaveRequestApproval rows rather than off the dashboard's access
    gate: being able to SEE the queue is not the same as being an approver on
    a particular request, and listing someone else's decisions as this user's
    to make would send them to a page with no buttons.
    """
    from django.urls import reverse
    from .models import LeaveRequestApproval

    rows = (LeaveRequestApproval.objects
            .filter(approver=user, decision='pending',
                    leave_request__status='pending')
            .select_related('leave_request', 'leave_request__employee',
                            'leave_request__leave_type')
            .order_by('leave_request__created_at'))
    items = []
    for row in rows:
        req = row.leave_request
        items.append(_item(
            title=req.employee.full_name,
            subtitle=f'{req.leave_type.name} · {req.start_date:%d %b} – {req.end_date:%d %b %Y}',
            when=req.created_at,
            url=reverse('hr:leave_request_detail', args=[req.pk]),
            meta=f'{req.days} day{"" if req.days == 1 else "s"}'))
    return _group('leave', 'Leave requests', 'bi-calendar2-check',
                  reverse('hr:leave_request_list'), items)


def leave_revokes(user):
    """Requests to void an already-approved leave. Decided by the dashboard
    roster rather than by named approvers, so it is gated the same way the
    dashboard itself is."""
    from django.urls import reverse
    from .views import can_view_leave_dashboard
    from .models import LeaveRevokeRequest

    if not can_view_leave_dashboard(user):
        return None
    rows = (LeaveRevokeRequest.objects.filter(status='pending')
            .select_related('leave_record', 'leave_record__employee')
            .order_by('created_at'))
    items = [
        _item(title=r.leave_record.employee.full_name,
              subtitle=f'Cancel leave {r.leave_record.start_date:%d %b} – {r.leave_record.end_date:%d %b %Y}',
              when=r.created_at,
              url=reverse('hr:leave_request_list'))
        for r in rows
    ]
    return _group('leave_revoke', 'Leave cancellations', 'bi-calendar2-x',
                  reverse('hr:leave_request_list'), items)


# ── Attendance ───────────────────────────────────────────────────────────────

def attendance_exceptions(user):
    """Exception requests from this user's own reports.

    Scoped exactly as hr.context_processors.pending_counts scopes the sidebar
    badge — direct and secondary reports, never the viewer's own request,
    which the queue excludes rather than merely disabling.
    """
    from django.urls import reverse
    from .views import can_view_team_exceptions
    from .models import AttendanceException, AttendanceExceptionRevokeRequest

    if not can_view_team_exceptions(user):
        return None
    emp = getattr(user, 'employee_profile', None)
    items = []
    if emp is not None:
        active = (AttendanceException.objects
                  .filter(status__in=('pending', 'expired'))
                  .exclude(employee__user=user)
                  .select_related('employee')
                  .order_by('event_date', 'event_start_time'))
        mine = (active.filter(employee__main_manager=emp)
                | active.filter(employee__secondary_managers=emp)).distinct()
        for exc in mine:
            items.append(_item(
                title=exc.employee.full_name,
                subtitle=(exc.custom_reason.strip()
                          or exc.get_reason_category_display()),
                when=exc.created_at,
                url=reverse('hr:team_exceptions'),
                meta=f'{exc.event_date:%d %b %Y}'))

        revokes = (AttendanceExceptionRevokeRequest.objects
                   .filter(status='pending')
                   .select_related('attendance_exception',
                                   'attendance_exception__employee'))
        revokes = (revokes.filter(attendance_exception__employee__main_manager=emp)
                   | revokes.filter(attendance_exception__employee__secondary_managers=emp)
                   ).distinct()
        for rev in revokes:
            exc = rev.attendance_exception
            items.append(_item(
                title=exc.employee.full_name,
                subtitle='Asked to withdraw an approved exception',
                when=rev.created_at,
                url=reverse('hr:team_exceptions'),
                meta=f'{exc.event_date:%d %b %Y}'))
    return _group('attendance_exceptions', 'Attendance exceptions',
                  'bi-shield-check', reverse('hr:team_exceptions'), items)


def late_queries(user):
    """Challenges to a Late mark. HR-decided, matching the tab's own gate -
    TeamExceptionsView silently downgrades a non-HR user off this tab."""
    from django.urls import reverse
    from .models import LateQuery

    is_hr = bool(user.is_super_admin_user or user.is_admin_user
                 or user.is_erp_admin_user)
    if not is_hr:
        return None
    rows = (LateQuery.objects.filter(status='pending')
            .select_related('employee', 'attendance_record')
            .order_by('created_at'))
    items = [
        _item(title=q.employee.full_name,
              subtitle=q.message,
              when=q.created_at,
              url=reverse('hr:team_exceptions') + '?tab=late_queries',
              meta=f'{q.attendance_record.date:%d %b %Y}')
        for q in rows
    ]
    return _group('late_queries', 'Late-mark queries', 'bi-question-circle',
                  reverse('hr:team_exceptions') + '?tab=late_queries', items)


# ── Assets ───────────────────────────────────────────────────────────────────

def asset_handovers(user):
    """Handover forms waiting on this user's signature.

    Two different acts land here: authorising a handover somebody else raised,
    and signing for equipment being issued to or returned by you. Both are a
    signature this person owes, which is what the tab is for.
    """
    from django.urls import reverse
    from .models import AssetHandover

    emp = getattr(user, 'employee_profile', None)
    rows = list(AssetHandover.objects.filter(
        status='pending_authorization', authorizer=user
    ).select_related('employee', 'asset', 'vehicle'))
    to_authorize = {r.pk for r in rows}
    if emp is not None:
        rows += [r for r in AssetHandover.objects.filter(
            employee=emp,
            status__in=('pending_receipt', 'pending_return_confirmation')
        ).select_related('employee', 'asset', 'vehicle')
            if r.pk not in to_authorize]

    items = []
    for row in sorted(rows, key=lambda r: r.created_at):
        thing = row.asset or row.vehicle
        items.append(_item(
            title=str(thing) if thing else 'Asset handover',
            subtitle=('Authorise this handover'
                      if row.pk in to_authorize
                      else row.get_status_display()),
            when=row.created_at,
            url=reverse('hr:asset_handover_detail', args=[row.pk]),
            meta=row.employee.full_name))
    return _group('asset_handovers', 'Asset handovers', 'bi-box-seam',
                  reverse('hr:asset_handover_list'), items)


# ── Procurement ──────────────────────────────────────────────────────────────

def purchase_orders(user):
    """POs sitting on a stage this user is the one allowed to sign.

    Decided by the PO's own can_user_approve_stage() against its current
    stage, so this cannot list a PO whose Approve button would refuse them.
    The stage walk is Python-side because it depends on the value threshold
    that decides whether CEO sign-off is required; the queryset first cuts the
    set down to POs that cannot possibly be released.
    """
    from django.db.models import Q
    from django.urls import reverse
    try:
        from procurement.models import PurchaseOrder
        from procurement.views import _visible_pos_for
    except ImportError:
        return None

    open_pos = (_visible_pos_for(user)
                .filter(Q(coo_approved_at__isnull=True)
                        | Q(ceo_approved_at__isnull=True))
                .order_by('-id'))
    items = []
    for po in open_pos:
        stage = po.current_stage
        if not stage or not po.can_user_approve_stage(user, stage['key']):
            continue
        items.append(_item(
            title=po.po_number or f'PO #{po.pk}',
            subtitle=f'Awaiting {stage["label"]}',
            when=po.created_at,
            url=reverse('procurement:po_detail', args=[po.pk]),
            meta=getattr(po.project, 'project_name', '') or ''))
    return _group('purchase_orders', 'Purchase orders', 'bi-receipt',
                  reverse('procurement:po_list'), items)


SOURCES = (
    leave_requests,
    leave_revokes,
    attendance_exceptions,
    late_queries,
    asset_handovers,
    purchase_orders,
)


def pending_approvals(user):
    """[group, ...] for everything awaiting this user, most items first.

    A source that raises is dropped rather than taking the page down with it:
    this is an aggregator over half the system, and one module's problem
    should not cost someone the view of their other five queues.
    """
    groups = []
    for source in SOURCES:
        try:
            group = source(user)
        except Exception:      # noqa: BLE001 - see docstring
            continue
        if group is not None and group['count']:
            groups.append(group)
    groups.sort(key=lambda g: g['count'], reverse=True)
    return groups


def pending_approvals_count(user):
    return sum(g['count'] for g in pending_approvals(user))
