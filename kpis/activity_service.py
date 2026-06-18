"""Assemble the Team Activity overview + per-user breakdown from the activity
registry. One grouped query per metric for the overview (not per-user)."""
from django.contrib.auth import get_user_model

from .activity import ACTIVITY_METRICS, MODULE_ORDER, headline_metrics
from .periods import period_bounds, label_for, period_options


def activity_window(period):
    if period == 'all':
        return None, None
    return period_bounds(period)


def activity_period_options():
    return [('all', 'All time')] + period_options()


def _period_label(period):
    return 'All time' if period == 'all' else label_for(period)


def build_activity_overview(period, sort='total'):
    start, end = activity_window(period)
    metric_counts = {m.key: m.counts(start, end) for m in ACTIVITY_METRICS}
    heads = headline_metrics()
    head_keys = {m.key for m in heads}

    User = get_user_model()
    users = (User.objects.filter(is_active=True)
             .select_related('role')
             .order_by('first_name', 'last_name', 'username'))

    rows = []
    for u in users:
        counts = {m.key: metric_counts[m.key].get(u.id, 0) for m in ACTIVITY_METRICS}
        rows.append({
            'user': u,
            'total': sum(counts.values()),
            'headline': {k: counts[k] for k in head_keys},
            'headline_cells': [counts[m.key] for m in heads],
            'counts': counts,
        })

    if sort != 'total' and sort in head_keys:
        rows.sort(key=lambda r: r['headline'][sort], reverse=True)
    else:
        rows.sort(key=lambda r: r['total'], reverse=True)

    return {
        'period': period,
        'period_label': _period_label(period),
        'headline_metrics': heads,
        'rows': rows,
        'sort': sort if (sort == 'total' or sort in head_keys) else 'total',
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
