from django.conf import settings
from django.utils import timezone

from accounts.models import AI_DEVELOPER_ROLE_NAMES, User

from .models import DevTask, DevTaskUpdate, DevDigest


def build_digest_context(period=None):
    """Pure aggregation (no API). Returns a dict describing each developer's
    task progress for the AI prompt / fallback rendering."""
    devs = User.objects.filter(role__name__in=AI_DEVELOPER_ROLE_NAMES, is_active=True).order_by('username')
    out = {'generated_for': str(period or timezone.now().date()), 'developers': []}
    for dev in devs:
        tasks = list(DevTask.objects.filter(developer=dev))
        done = [t for t in tasks if t.status == 'done']
        on_time = [t for t in done if t.on_time is True]
        overdue = [t for t in tasks if t.is_overdue]
        stuck = [t for t in tasks if t.is_stuck]
        in_progress = [t for t in tasks if t.status == 'in_progress']
        # avg elapsed hours over completed tasks that have both stamps
        elapsed_hours = [t.elapsed.total_seconds() / 3600 for t in done if t.elapsed]
        avg_elapsed = round(sum(elapsed_hours) / len(elapsed_hours), 1) if elapsed_hours else None
        out['developers'].append({
            'name': dev.get_full_name() or dev.username,
            'assigned': len(tasks),
            'in_progress': len(in_progress),
            'done': len(done),
            'overdue': len(overdue),
            'on_time_rate': (round(100 * len(on_time) / len(done)) if done else None),
            'avg_elapsed_hours': avg_elapsed,
            'overdue_titles': [t.title for t in overdue],
            'stuck_titles': [t.title for t in stuck],
            'recent_notes': list(
                DevTaskUpdate.objects.filter(task__developer=dev)
                .values_list('note', flat=True)[:5]),
        })
    return out


def _fallback_text(ctx):
    lines = ['AI summary unavailable — set ANTHROPIC_API_KEY for narrative reports.',
             f"Progress snapshot for {ctx['generated_for']}:", '']
    for d in ctx['developers']:
        lines.append(f"• {d['name']}: {d['done']} done / {d['assigned']} assigned, "
                     f"{d['in_progress']} in progress, {d['overdue']} overdue"
                     + (f", on-time {d['on_time_rate']}%" if d['on_time_rate'] is not None else '')
                     + (f", avg {d['avg_elapsed_hours']}h/task" if d['avg_elapsed_hours'] else ''))
        if d['overdue_titles']:
            lines.append(f"    overdue: {', '.join(d['overdue_titles'])}")
        if d['stuck_titles']:
            lines.append(f"    stuck: {', '.join(d['stuck_titles'])}")
    if not ctx['developers']:
        lines.append('No developers found.')
    return '\n'.join(lines)


def _prompt(ctx):
    import json
    return (
        "You are a delivery-lead assistant. From this JSON of developer task progress, "
        "write a concise admin report with: (1) a short per-developer progress summary, "
        "(2) an Overdue / stuck alerts section, (3) a Time & velocity insight section "
        "(completion speed, estimate-vs-actual, trends), (4) a one-paragraph overall rollup. "
        "Use plain text with clear headings. Be specific and brief.\n\n"
        + json.dumps(ctx, indent=2, default=str))


def generate_admin_digest(generated_by=None, period=None):
    ctx = build_digest_context(period)
    key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    model = ''
    if not key:
        content = _fallback_text(ctx)
    else:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            model = getattr(settings, 'DEVTRACKING_AI_MODEL', 'claude-sonnet-4-6')
            msg = client.messages.create(
                model=model, max_tokens=1500,
                messages=[{'role': 'user', 'content': _prompt(ctx)}])
            content = msg.content[0].text
        except Exception:
            content, model = _fallback_text(ctx), ''
    return DevDigest.objects.create(
        period_date=(period or timezone.now().date()), scope='all',
        content=content, model_used=model, generated_by=generated_by)
