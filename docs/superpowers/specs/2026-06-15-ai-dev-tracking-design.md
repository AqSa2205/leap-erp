# AI Development Tracking — Design

**Date:** 2026-06-15
**Goal:** A new ERP module to track developers' assigned tasks, completion, and time — with live GitHub PR status on code tasks and AI-generated progress reports for the admin. Not a timesheet: devs self-serve with two clicks + a one-liner; timing is automatic.

## Users & roles
- The 3 hires (intern, software engineer, junior software engineer) are `accounts.User` rows with a **new `DEVELOPER` role**.
- **Admin/super_admin** assign tasks and read reports. **Developers** see only their own tasks ("My Tasks").

## Decisions (from stakeholder)
- **Time tracking:** due date + optional estimated hours set at assignment; system auto-stamps `started_at`/`completed_at`; derives elapsed time and on-time-vs-late. No manual hour logging.
- **Who/cadence:** devs self-update their tasks; AI admin digest builds daily (management command for cron) **and** on demand.
- **AI outputs (all):** per-dev progress summary, overdue/stuck alerts, time & velocity insight, one rolled-up admin digest.
- **GitHub:** optional per task; **live PR status in v1** (state/merged/commit count), with graceful fallback when no token.

## Data model (new app `devtracking`)

### `DevTask`
- `developer` → FK User (assignee), `assigned_by` → FK User
- `title` (char), `description` (text, blank)
- `priority` — `low` / `medium` / `high` (default medium)
- `status` — `assigned` / `in_progress` / `blocked` / `done` (default `assigned`)
- `estimated_hours` — Decimal, null/blank
- `due_date` — Date, null/blank
- `started_at` / `completed_at` — DateTime, null/blank (auto-stamped on status change)
- `github_url` — URL, blank (a PR/branch link for code tasks)
- GitHub status cache: `gh_state` (open/closed/merged/''), `gh_commits` (int null), `gh_title` (char blank), `gh_checked_at` (DateTime null)
- `created_at` / `updated_at`
- **Derived properties:** `elapsed` (completed_at−started_at, or now−started_at while in progress), `is_overdue` (due_date < today and status != done), `on_time` (completed_at.date() ≤ due_date when done), `is_stuck` (in_progress and started_at older than N days with no recent update).
- **`mark_started()` / `mark_done()` / `mark_blocked()`** helpers stamp the timestamps idempotently (don't overwrite an existing `started_at`).

### `DevTaskUpdate`
- `task` → FK DevTask, `author` → FK User, `note` (text), `status_changed_to` (char blank), `created_at`. The dev's quick "what I did" log — drives the AI summary.

### `DevDigest`
- `period_date` (Date), `scope` (`all` or a developer id as text), `content` (text — AI markdown), `model_used` (char), `generated_by` → FK User null, `generated_at`. Cached so pages don't re-call the API; regenerated on demand / daily.

## AI service (`devtracking/ai.py`)
- Uses the **Anthropic SDK**; `ANTHROPIC_API_KEY` + `DEVTRACKING_AI_MODEL` (default `claude-sonnet-4-6`) from settings/env.
- `build_digest_context()` — pure function assembling structured data (each dev: tasks by status, completion counts, on-time rate, avg elapsed, estimate-vs-actual, overdue/stuck lists, recent notes). Unit-testable with no API.
- `generate_admin_digest(period=today, generated_by=None)` — formats the context into a prompt, calls Claude, returns digest text covering: per-dev summary, overdue/stuck alerts, time & velocity insight, overall rollup. Saves a `DevDigest`.
- **Graceful fallback:** if `ANTHROPIC_API_KEY` is unset or the call errors, return a plain-text rendering of `build_digest_context()` with a "AI summary unavailable — configure ANTHROPIC_API_KEY" header. The module is fully usable without the key.
- All tests mock the Anthropic client or exercise the fallback — **never hit the network**.

## GitHub live status (`devtracking/github.py`)
- `parse_pr_url(url)` → `(owner, repo, number)` or None (handles `https://github.com/{owner}/{repo}/pull/{n}`).
- `fetch_pr_status(url)` → dict `{state, merged, commits, title}` via `GET /repos/{owner}/{repo}/pulls/{n}` using `requests` with `GITHUB_TOKEN` (Bearer) when set; returns None on any failure (no token, non-PR url, network/404). Uses a short timeout.
- `refresh_task_github(task)` — calls `fetch_pr_status`, writes the `gh_*` cache fields + `gh_checked_at`. Called: on the task detail view if cache is stale (> 15 min) or absent, and via a "Refresh" button. Never blocks the page on failure.
- Tests mock `requests` / the fetch — no network.

## Views & templates
**Admin (capability `devtracking.admin`):**
- **Dashboard** (`/devtracking/`): board overview (per-dev counts assigned/in-progress/done/overdue), the latest AI digest with a **Generate now** button, overdue/stuck call-outs.
- **Assign Task** form: developer, title, description, priority, estimated hours, due date, optional GitHub URL. On save → notify the developer (`notify_users`).
- **Task list**: filter by developer/status/overdue; each row shows time + on-time/late + GitHub state badge.
- **Per-dev detail**: that dev's tasks + timeline of updates + their AI per-dev summary.

**Developer (capability `devtracking.mywork`):**
- **My Tasks**: their tasks grouped by status; **Start** / **Done** / **Blocked** buttons (stamp timestamps) and an "add note" box (one line). Read-only on others' tasks.

## Permissions & nav
- New capability module `devtracking` in `accounts/permissions.py` with codenames `devtracking.admin` (admin/super_admin) and `devtracking.mywork` (developer + admins). Seed defaults.
- Nav (`base.html`): admin link "Dev Tracking" under Administration; a "My Tasks" link visible to anyone with `devtracking.mywork`.

## Reporting cadence
- `python manage.py generate_dev_digest` — generates today's `all` digest (for Render Cron, "every morning").
- On-demand "Generate now" button calls the same path. Both no-op gracefully without the API key (fallback digest).

## Dependencies / setup
- Add `anthropic` and `requests` to `requirements.txt`.
- Settings read from env: `ANTHROPIC_API_KEY`, `DEVTRACKING_AI_MODEL` (default `claude-sonnet-4-6`), `GITHUB_TOKEN`. All optional — absence degrades gracefully (no AI summary / no live PR status), never crashes.

## Out of scope (v1)
- Auto-discovering a dev's commits across a repo (we link explicit PRs per task).
- Email delivery of the digest (it's viewable in-app; notifications already exist for assignment).
- Sprints/epics, story points, time-off integration.

## Testing
- Model: status helpers stamp `started_at`/`completed_at` once; `is_overdue`/`on_time`/`elapsed` correct.
- Assign flow: creates task + notifies dev; dev Start/Done stamps + on-time computed; note creates `DevTaskUpdate`.
- Permissions: a developer can't see/assign others' tasks; admin can; nav capability gating.
- AI: `build_digest_context` aggregates correctly; `generate_admin_digest` fallback path works with no key (mocked).
- GitHub: `parse_pr_url` cases; `refresh_task_github` writes cache from a mocked fetch; no-token returns None gracefully.
