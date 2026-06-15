import re
import requests
from django.conf import settings
from django.utils import timezone

_PR_RE = re.compile(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)')


def parse_pr_url(url):
    m = _PR_RE.search(url or '')
    return (m.group(1), m.group(2), int(m.group(3))) if m else None


def fetch_pr_status(url):
    """Return {'state','commits','title'} for a GitHub PR URL, or None on any
    failure (bad/non-PR url, no network, non-200). state is 'merged' when merged,
    else the API 'state' (open/closed)."""
    parsed = parse_pr_url(url)
    if not parsed:
        return None
    owner, repo, number = parsed
    headers = {'Accept': 'application/vnd.github+json'}
    token = getattr(settings, 'GITHUB_TOKEN', '')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        r = requests.get(f'https://api.github.com/repos/{owner}/{repo}/pulls/{number}',
                         headers=headers, timeout=6)
        if r.status_code != 200:
            return None
        d = r.json()
        state = 'merged' if d.get('merged') else d.get('state', '')
        return {'state': state, 'commits': d.get('commits'), 'title': d.get('title', '')}
    except Exception:
        return None


def refresh_task_github(task):
    """Fetch + cache onto the task. Returns True if updated, False on failure."""
    status = fetch_pr_status(task.github_url)
    if status is None:
        return False
    task.gh_state = status['state'] or ''
    task.gh_commits = status['commits']
    task.gh_title = (status['title'] or '')[:300]
    task.gh_checked_at = timezone.now()
    task.save(update_fields=['gh_state', 'gh_commits', 'gh_title', 'gh_checked_at'])
    return True


def refresh_if_stale(task, max_age_minutes=15):
    """Refresh a task's GitHub cache if it has a PR url and the cache is missing
    or older than max_age_minutes. Swallows all errors (never blocks a page)."""
    if not task.github_url:
        return
    from datetime import timedelta
    fresh = task.gh_checked_at and (timezone.now() - task.gh_checked_at) < timedelta(minutes=max_age_minutes)
    if fresh:
        return
    try:
        refresh_task_github(task)
    except Exception:
        pass
