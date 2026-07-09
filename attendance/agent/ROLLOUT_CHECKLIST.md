# Leap Wi-Fi Attendance — Per-Laptop Install Checklist

Do this **once per laptop**. Each laptop needs its **own token**.

## Before you start (once)
- [ ] Have the **`LeapAttendanceAgent`** folder (contains `LeapAttendanceAgent.exe`, `install.ps1`, `uninstall.ps1`).
- [ ] Have the **Token map CSV** open (HR → Wi-Fi Devices → *Token map (CSV)*).
      Columns: `employee, iqama_number, device_label, serial_number, token`.
- [ ] Make sure each laptop is **registered** as a device in HR → Wi-Fi Devices (so it has a token).

## On each laptop
1. [ ] Copy the **`LeapAttendanceAgent`** folder onto the laptop (USB or shared folder).
2. [ ] Find **this laptop's row** in the CSV (match by employee / serial number) → copy its **token**.
3. [ ] Open **PowerShell as Administrator** (Start → type *PowerShell* → right-click → **Run as administrator**).
4. [ ] Run (paste this laptop's token):
       ```
       cd "C:\path\to\LeapAttendanceAgent"
       .\install.ps1 -Token "PASTE_THIS_LAPTOPS_TOKEN"
       ```
       If it blocks on script policy, use:
       `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Token "PASTE_TOKEN"`
5. [ ] A **popup** appears — it must say **"SUCCESS - attendance recorded (Present)"**.
       - `NOT counted: off_network` → not on the office Wi-Fi (or on Ethernet — see below).
       - `NOT counted: off_hours` → outside 06:00–20:00.
       - `NO TOKEN` → the token wasn't passed correctly; re-run step 4.
6. [ ] Done — this laptop now marks the employee **Present** automatically every day.

## Re-check anytime (no reinstall)
Run this on the laptop to fire a test and see the result popup:
```
"C:\ProgramData\LeapAttendance\LeapAttendanceAgent.exe" --test
```
(Result is also written to `C:\ProgramData\LeapAttendance\last-test.txt`.)

## If something snags
- **SmartScreen / antivirus warns** → right-click `LeapAttendanceAgent.exe` → Properties → tick **Unblock**. If AV quarantines it, add an exclusion for `C:\ProgramData\LeapAttendance`.
- **"file in use"** → run `.\uninstall.ps1` first, then re-run install.
- **Wrong person shows present** → you used the wrong token; re-run install with the correct one for that laptop.

## Important
- ⚠️ **Ethernet / docked users:** the agent reads the **Wi-Fi** name — a laptop on a wired dock (Wi-Fi off) won't be detected. Ask those users to keep Wi-Fi on, or flag them.
- 🔒 **Tell staff** attendance is tracked via network + activity **before** switch-on.

## To remove from a laptop
Admin PowerShell → `.\uninstall.ps1`
