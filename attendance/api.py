"""The agent-facing check-in endpoint.

POST /api/attendance/checkin/  (JSON, no session — token-authenticated)
    { "token": "...", "bssid": "a1:b2:c3:d4:e5:f6", "idle_seconds": 12, "hostname": "LT-042" }

A ping is *counted* only when: the device token is valid & active, the BSSID is
a registered active office AP, the user is active (idle <= ATT_MAX_IDLE_SECONDS)
and it's within work hours. Counted pings roll into the employee's AttendanceDay.
Always returns JSON so the agent can log the outcome.
"""
import json
from datetime import time as _time

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import OfficeNetwork, RegisteredDevice, Heartbeat, AttendanceDay


def _cfg(name, default):
    return getattr(settings, name, default)


def _parse_hhmm(value, fallback):
    try:
        h, m = str(value).split(':')
        return _time(int(h), int(m))
    except (ValueError, AttributeError):
        return fallback


def _within_work_window(local_dt):
    start = _parse_hhmm(_cfg('ATT_WORK_START', '06:00'), _time(6, 0))
    end = _parse_hhmm(_cfg('ATT_WORK_END', '20:00'), _time(20, 0))
    return start <= local_dt.time() <= end


@csrf_exempt
@require_POST
def checkin(request):
    try:
        body = json.loads((request.body or b'{}').decode() or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'bad json'}, status=400)

    token = (body.get('token') or '').strip()
    bssid = (body.get('bssid') or '').strip().lower()
    try:
        idle = max(0, int(body.get('idle_seconds') or 0))
    except (TypeError, ValueError):
        idle = 0
    hostname = str(body.get('hostname') or '')[:120]

    device = (RegisteredDevice.objects
              .filter(token=token, is_active=True)
              .select_related('employee').first())
    if not device:
        return JsonResponse({'ok': False, 'error': 'unknown or inactive device'}, status=401)

    now = timezone.now()
    local = timezone.localtime(now)

    # Decide whether this ping counts, and if not, why.
    counted, reason = True, ''
    if not OfficeNetwork.objects.filter(bssid=bssid, is_active=True).exists():
        counted, reason = False, 'off_network'
    elif idle > int(_cfg('ATT_MAX_IDLE_SECONDS', 300)):
        counted, reason = False, 'idle'
    elif not _within_work_window(local):
        counted, reason = False, 'off_hours'

    Heartbeat.objects.create(
        device=device, bssid=bssid, idle_seconds=idle,
        counted=counted, reason=reason, hostname=hostname)
    device.last_seen_at = now
    device.save(update_fields=['last_seen_at'])

    if counted:
        day, _ = AttendanceDay.objects.get_or_create(
            employee=device.employee, date=local.date())
        this_minute = local.replace(second=0, microsecond=0)
        prev_minute = (timezone.localtime(day.last_seen).replace(second=0, microsecond=0)
                       if day.last_seen else None)
        # Count each wall-clock minute at most once (agent pings ~1/min).
        if prev_minute is None or prev_minute < this_minute:
            day.active_minutes += 1
        if day.first_seen is None:
            day.first_seen = now
        day.last_seen = now
        day.is_present = day.active_minutes >= int(_cfg('ATT_MIN_MINUTES_PRESENT', 60))
        day.save()

        # Mirror into the existing HR attendance grid. Never let a sync hiccup
        # break heartbeat ingestion.
        try:
            from .services import sync_hr_attendance
            sync_hr_attendance(day)
        except Exception:
            pass

    return JsonResponse({'ok': True, 'counted': counted, 'reason': reason})
