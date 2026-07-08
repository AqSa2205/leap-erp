<#
    Remove the Leap Attendance agent from a laptop. Run as administrator:
        powershell -ExecutionPolicy Bypass -File uninstall.ps1
#>
$ErrorActionPreference = 'SilentlyContinue'
Stop-ScheduledTask -TaskName 'LeapAttendanceAgent'
Unregister-ScheduledTask -TaskName 'LeapAttendanceAgent' -Confirm:$false
Get-Process -Name 'LeapAttendanceAgent' | Stop-Process -Force
Remove-Item -Recurse -Force (Join-Path $env:ProgramData 'LeapAttendance')
Write-Host "Leap Attendance agent removed." -ForegroundColor Yellow
