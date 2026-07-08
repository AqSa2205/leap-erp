# Wi-Fi Automatic Attendance

An agent on each employee laptop pings the ERP every minute with the Wi-Fi
**BSSID** it's connected to and the user's **idle time**. The ERP counts a ping
only when the laptop is on a **registered office access point**, the user is
**active**, and it's **within work hours**, and rolls counted pings into a
per-employee-per-day `AttendanceDay`.

## Endpoint

`POST /api/attendance/checkin/` — JSON, token-authenticated (no session/CSRF).

```json
{ "token": "<device token>", "bssid": "a1:b2:c3:d4:e5:f6", "idle_seconds": 12, "hostname": "LT-042" }
```

Response: `{ "ok": true, "counted": true|false, "reason": "off_network|idle|off_hours" }`.
Unknown/inactive token → `401`.

## Models (admin: "Wi-Fi Attendance")

- **OfficeNetwork** — one row per office AP (BSSID, lowercase). A ping only
  counts on one of these.
- **RegisteredDevice** — one per laptop, linked to an `hr.Employee`; each row
  auto-generates a unique `token`. Untick `is_active` to revoke.
- **Heartbeat** — raw pings (one/machine/minute). Prune regularly.
- **AttendanceDay** — derived `first_seen` / `last_seen` / `active_minutes` /
  `is_present` per employee per day.

## Settings (erp_leap/settings.py)

| setting | default | meaning |
|---|---|---|
| `ATT_MAX_IDLE_SECONDS` | 300 | idle longer than this → ping doesn't count |
| `ATT_WORK_START` / `ATT_WORK_END` | `06:00` / `20:00` | local (Riyadh) work window |
| `ATT_MIN_MINUTES_PRESENT` | 60 | active minutes to mark the day present |

## Operations

- **Register APs:** on an office machine run `netsh wlan show interfaces` to read
  each AP's BSSID; add one `OfficeNetwork` per AP (cover every AP employees use).
- **Register devices:** add one `RegisteredDevice` per laptop; export the
  device→token map to provision agents.
- **Prune heartbeats:** `python manage.py prune_heartbeats --days 30`
  (schedule daily — the table grows by one row per machine per minute).

## Agent (Phase 3)

`attendance/agent/agent.py` — Windows agent. Set `ENDPOINT`, then
`pyinstaller --onefile --noconsole agent.py` → `agent.exe`. Provision each
machine's token via env var `LEAP_ATT_TOKEN` or
`%PROGRAMDATA%\LeapAttendance\config.json`. Deploy via GPO/Intune with a
"run at logon, hidden, restart on failure" scheduled task.

## Notes

- BSSID is a **soft** control (discoverable/spoofable) — good for honest
  attendance, not tamper-proof. The device token is the real credential; keep
  it over HTTPS and revoke via `is_active`.
- Disclose network+activity tracking to employees before go-live.
