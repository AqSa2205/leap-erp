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
@{ token = $Token } | ConvertTo-Json | Set-Content -Path (Join-Path $dir 'config.json') -Encoding UTF8

# Run in the logged-in user's session (needed to read Wi-Fi + idle time), at
# logon, hidden, and auto-restart if it ever exits.
$action    = New-ScheduledTaskAction -Execute $exe
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                 -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings `
                       -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $task

Write-Host "Leap Attendance agent installed and started (task '$task', runs at every logon)." -ForegroundColor Green
