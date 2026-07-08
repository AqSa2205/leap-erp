"""Leap ERP — Wi-Fi attendance agent (Windows).

Runs on each employee laptop. Every minute it reads the BSSID of the connected
Wi-Fi and how long the user has been idle, and POSTs a heartbeat to the ERP.
The ERP decides whether it counts (office AP + active + work hours).

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
import time
import urllib.request

# Set this to your cloud ERP base URL before building the exe.
ENDPOINT = "https://YOUR-ERP-HOST/api/attendance/checkin/"
INTERVAL_SECONDS = 60
CONFIG_PATH = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                           "LeapAttendance", "config.json")


def load_token():
    token = os.environ.get("LEAP_ATT_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
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
        s = line.strip()
        if s.lower().startswith("bssid"):
            # "BSSID                  : a1:b2:c3:d4:e5:f6"
            return s.split(":", 1)[1].strip().lower()
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
            resp.read()
    except Exception:
        pass  # offline / ERP down — just try again next tick


def main():
    token = load_token()
    if not token:
        return  # not provisioned yet
    while True:
        send(token)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
