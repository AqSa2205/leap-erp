"""Assemble the Team Activity overview + per-user breakdown from the activity
registry. One grouped query per metric for the overview (not per-user)."""
from django.contrib.auth import get_user_model

from .activity import (
    ACTIVITY_METRICS, ACTIVITY_BY_KEY, MODULE_ORDER, CYCLE_LABELS,
    DEPARTMENT_ACTIVITY, all_cycle_data,
)
from .periods import period_bounds, label_for, period_options


def activity_window(period):
    if period == 'all':
        return None, None
    return period_bounds(period)


def activity_period_options():
    return [('all', 'All time')] + period_options()


def _period_label(period):
    return 'All time' if period == 'all' else label_for(period)


# Every role maps to a department bucket for the region → department grouping.
ACTIVITY_DEPARTMENTS = [
    ('management', 'Management'),
    ('sales', 'Sales'),
    ('proposal', 'Proposal'),
    ('procurement', 'Procurement'),
    ('finance', 'Finance'),
    ('ai', 'AI / Development'),
    ('other', 'Other'),
]
_DEPT_LABEL = dict(ACTIVITY_DEPARTMENTS)
_DEPT_ORDER = [d for d, _ in ACTIVITY_DEPARTMENTS]
ROLE_TO_DEPT = {
    'super_admin': 'management', 'admin': 'management', 'manager': 'management',
    'sales_rep': 'sales',
    'proposal_head': 'proposal', 'proposal_rep': 'proposal',
    'procurement_mgr': 'procurement', 'procurement_off': 'procurement',
    'finance_head': 'finance', 'finance_manager': 'finance', 'finance_rep': 'finance',
    'developer': 'ai', 'ai_head': 'ai', 'ai_intern': 'ai',
    'ai_engineer': 'ai', 'ai_junior_engineer': 'ai',
}


def build_activity_overview(period):
    """Team activity grouped region → department, where each department shows
    only the actions it owns (count columns) plus its workflow stage cycle-time
    (avg-days column). Totals count a person's own-department actions only."""
    start, end = activity_window(period)
    metric_counts = {m.key: m.counts(start, end) for m in ACTIVITY_METRICS}
    cycle_data = all_cycle_data(start, end)

    User = get_user_model()
    users = (User.objects.filter(is_active=True)
             .select_related('role', 'region')
             .order_by('first_name', 'last_name', 'username'))

    # region_id -> {region, users: [...]}. A person is placed into every
    # department they ACTUALLY did work in (not just their role's) so that, e.g.,
    # a manager who starts costing is credited under Sales — bias-free tracking.
    region_map = {}
    for u in users:
        region_map.setdefault(u.region_id, {'region': u.region, 'users': []})['users'].append(u)

    def _home_dept(u):
        return ROLE_TO_DEPT.get(u.role.name if u.role else None, 'other')

    def _has_dept_activity(u, count_keys, cycle_keys):
        if any(metric_counts[k].get(u.id, 0) for k in count_keys):
            return True
        return any(cycle_data.get(k, {}).get(u.id) is not None for k in cycle_keys)

    def _build_dept(dkey, region_users):
        cfg = DEPARTMENT_ACTIVITY.get(dkey, {'metrics': [], 'cycles': []})
        count_keys, cycle_keys = cfg['metrics'], cfg['cycles']
        columns = [{'key': k, 'label': ACTIVITY_BY_KEY[k].label, 'kind': 'count'} for k in count_keys]
        columns += [{'key': k, 'label': CYCLE_LABELS[k], 'kind': 'cycle'} for k in cycle_keys]

        rows, dept_total = [], 0
        for u in region_users:
            home = _home_dept(u) == dkey
            # Show a person here if it's their home department OR they did any of
            # this department's work (crediting cross-department contributions).
            if not home and not _has_dept_activity(u, count_keys, cycle_keys):
                continue
            cells, total = [], 0
            for k in count_keys:
                v = metric_counts[k].get(u.id, 0)
                total += v
                cells.append({'kind': 'count', 'display': v})
            for k in cycle_keys:
                v = cycle_data.get(k, {}).get(u.id)
                cells.append({'kind': 'cycle', 'display': f'{v}d' if v is not None else '—'})
            rows.append({'user': u, 'cells': cells, 'total': total, 'is_home': home})
            dept_total += total
        if not rows:
            return None
        # Most active first; a genuine contributor outranks a zero-activity home member.
        rows.sort(key=lambda r: (r['total'], r['is_home']), reverse=True)
        return {'key': dkey, 'label': _DEPT_LABEL[dkey], 'columns': columns,
                'rows': rows, 'total': dept_total}

    regions, grand_total = [], 0
    for info in region_map.values():
        departments = [d for d in (_build_dept(dk, info['users']) for dk in _DEPT_ORDER) if d]
        region_total = sum(d['total'] for d in departments)
        people = len({r['user'].id for d in departments for r in d['rows']})
        grand_total += region_total
        regions.append({
            'region': info['region'],
            'name': info['region'].name if info['region'] else 'No region',
            'departments': departments,
            'total': region_total,
            'people': people,
        })
    regions.sort(key=lambda x: (x['region'] is None, (x['name'] or '').lower()))

    return {
        'period': period,
        'period_label': _period_label(period),
        'regions': regions,
        'grand_total': grand_total,
    }


def build_user_activity(period, user):
    start, end = activity_window(period)
    modules = []
    total = 0
    for module in MODULE_ORDER:
        items = []
        for m in ACTIVITY_METRICS:
            if m.module != module:
                continue
            c = m.count_for(start, end, user.id)
            total += c
            items.append({'label': m.label, 'count': c})
        modules.append({'module': module, 'items': items})
    return {
        'period': period,
        'period_label': _period_label(period),
        'user': user,
        'total': total,
        'modules': modules,
    }
