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

$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                 -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigLogon, $trigDay `
                       -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $task

Write-Host "Leap Attendance agent installed and started (task '$task', runs at every logon)." -ForegroundColor Green
