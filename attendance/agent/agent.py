r"""Leap ERP — Wi-Fi attendance agent (Windows).

Runs on each employee laptop. The scheduled task fires it **at logon**, and it
sends **one** check-in per launch (a once-a-day "the laptop connected at the
office today" mark), reading the connected Wi-Fi BSSID and the user's idle time.
Right after logon the Wi-Fi may not be up yet, so it retries briefly until the
ERP records the visit (counted), then exits. The ERP decides whether it counts
(office AP + active + work hours).

Config (per machine) — token is injected at deploy time, never hard-coded:
  - Env var  LEAP_ATT_TOKEN, or
  - File     %PROGRAMDATA%\LeapAttendance\config.json  ->  {"token": "..."}

Build a standalone exe (no Python needed on laptops):
  pyinstaller --onefile --noconsole agent.py     ->  dist/agent.exe
"""
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

# Set this to your cloud ERP base URL before building the exe.
ENDPOINT = "https://leap-erp.onrender.com/api/attendance/checkin/"
# One check-in per launch: retry until the ERP records it (Wi-Fi lags for a few
# seconds after wake/logon), then exit. Short interval so it succeeds quickly.
RETRY_MAX = 30
RETRY_INTERVAL_SECONDS = 10
CONFIG_PATH = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                           "LeapAttendance", "config.json")


def load_token():
    token = os.environ.get("LEAP_ATT_TOKEN", "").strip()
    if token:
        return token
    try:
        # utf-8-sig tolerates a BOM (PowerShell 5.1 writes one) so json.load
        # doesn't choke on the first byte.
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
            return (json.load(fh).get("token") or "").strip()
    except (OSError, ValueError):
        return ""


def current_bssid():
    """Read the connected AP's BSSID via netsh (Windows). '' if not connected."""
    try:
        out = subprocess.check_output(
            ["netsh", "wlan", "show", "interfaces"],
            stderr=subprocess.DEVNULL, creationflags=0x08000000,  # no window
        ).decode(errors="ignore")
    except Exception:
        return ""
    for line in out.splitlines():
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        # Windows labels this "BSSID" or (Win 11) "AP BSSID".
        if "bssid" in label.lower():
            return value.strip().lower()
    return ""


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    """Seconds since the last keyboard/mouse input (Windows)."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
            return max(0, millis // 1000)
    except Exception:
        pass
    return 0


def send(token):
    """POST one check-in. Returns the parsed JSON reply, or None if the request
    failed (offline / Wi-Fi not up yet / ERP unreachable)."""
    payload = json.dumps({
        "token": token,
        "bssid": current_bssid(),
        "idle_seconds": idle_seconds(),
        "hostname": socket.gethostname(),
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode() or "{}")
    except Exception:
        return None


def run_test():
    """One-off diagnostic: do a single check-in and show/log the result, so an
    installer can confirm a laptop works without opening the ERP.
    Invoke with:  LeapAttendanceAgent.exe --test
    """
    token = load_token()
    bssid = current_bssid()
    if not token:
        msg = "NO TOKEN configured.\nRun install.ps1 with this device's token."
    else:
        result = send(token)
        if result is None:
            msg = f"Could NOT reach the ERP.\nWi-Fi BSSID: {bssid or '(not connected)'}\nCheck internet / Wi-Fi."
        elif result.get("counted"):
            msg = f"SUCCESS - attendance recorded (Present).\nWi-Fi BSSID: {bssid}"
        else:
            msg = ("Reached ERP but NOT counted.\n"
                   f"Wi-Fi BSSID: {bssid or '(not connected)'}\n"
                   f"Reason: {result.get('reason')}")
    try:
        with open(os.path.join(os.path.dirname(CONFIG_PATH), "last-test.txt"),
                  "w", encoding="utf-8") as fh:
            fh.write(msg)
    except OSError:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "Leap Attendance - Test", 0x40)
    except Exception:
        print(msg)


def main():
    token = load_token()
    if not token:
        return  # not provisioned yet
    # One check-in per launch. Wait for Wi-Fi to (re)connect before sending —
    # right after wake/logon there's no BSSID for a few seconds, so skip the
    # server round-trip until we have one; then send until the ERP counts it.
    for _ in range(RETRY_MAX):
        if current_bssid():
            result = send(token)
            if result and result.get("counted"):
                break
        time.sleep(RETRY_INTERVAL_SECONDS)


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_test()
    else:
        main()
