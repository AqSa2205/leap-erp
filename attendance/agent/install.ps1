<#
    Leap Attendance agent — per-laptop installer.

    Run in an ELEVATED PowerShell (Run as administrator), from the folder that
    contains LeapAttendanceAgent.exe, passing THIS laptop's token:

        powershell -ExecutionPolicy Bypass -File install.ps1 -Token "PASTE_DEVICE_TOKEN"

    The token for each laptop comes from HR -> Wi-Fi Devices -> Token map (CSV).
    It installs the agent to C:\ProgramData\LeapAttendance, writes the token,
    and creates a hidden scheduled task that runs it at every logon (and
    restarts it if it stops). One exe serves every machine — only the token differs.
#>
param(
    [Parameter(Mandatory = $true)][string]$Token
)

$ErrorActionPreference = 'Stop'
$dir  = Join-Path $env:ProgramData 'LeapAttendance'
$exe  = Join-Path $dir 'LeapAttendanceAgent.exe'
$src  = Join-Path $PSScriptRoot 'LeapAttendanceAgent.exe'
$task = 'LeapAttendanceAgent'

if (-not (Test-Path $src)) { throw "LeapAttendanceAgent.exe not found next to this script ($src)." }

New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item -Path $src -Destination $exe -Force
# Write config.json as UTF-8 WITHOUT a BOM (Set-Content -Encoding UTF8 adds one
# on PowerShell 5.1, which the agent's JSON parser can't read).
$json = @{ token = $Token } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $dir 'config.json'), $json, (New-Object System.Text.UTF8Encoding($false)))

# Runs in the user's session (needs it to read Wi-Fi + idle). The agent sends one
# check-in and exits, so it's fired on:
#   - logon (fresh sign-in), and
#   - a repeating schedule every 30 min, 06:00-20:00.
# With -StartWhenAvailable, a run missed while the laptop was asleep fires right
# after it wakes — so waking + Windows-Hello (an UNLOCK, not a logon) still marks
# attendance. Re-running the same day is harmless (idempotent).
$action = New-ScheduledTaskAction -Execute $exe

$trigLogon = New-ScheduledTaskTrigger -AtLogOn
$trigDay   = New-ScheduledTaskTrigger -Daily -At 6:00am
$trigDay.Repetition = (New-ScheduledTaskTrigger -Once -At 6:00am `
                          -RepetitionInterval (New-TimeSpan -Minutes 30) `
                          -RepetitionDuration (New-TimeSpan -Hours 14)).Repetition

# On workstation UNLOCK (waking + Windows Hello / PIN is an unlock, not a logon,
# so this is what fires the instant someone sits back down at the office).
$scClass    = Get-CimClass -Namespace ROOT\Microsoft\Windows\TaskScheduler `
                           -ClassName MSFT_TaskSessionStateChangeTrigger
$trigUnlock = New-CimInstance -CimClass $scClass -ClientOnly -Property @{
                  StateChange = 8   # TASK_SESSION_UNLOCK
                  UserId      = "$env:USERDOMAIN\$env:USERNAME"
                  Enabled     = $true
              }

$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                 -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
                 -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigLogon, $trigDay, $trigUnlock `
                       -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $task

Write-Host "Leap Attendance agent installed (task '$task': logon + unlock + 30-min)." -ForegroundColor Green
# Instant confirmation: fire a one-off test check-in that pops the result.
Start-Process -FilePath $exe -ArgumentList '--test'
Write-Host "A popup will show whether this laptop's check-in COUNTED." -ForegroundColor Cyan
