$ErrorActionPreference = "Stop"
$TaskName = "ResearchReportLoopMemoryDreaming"
$Runner = Join-Path $PSScriptRoot "run-memory-maintenance-workbuddy.ps1"
if (-not (Test-Path $Runner)) { throw "Maintenance runner not found: $Runner" }

$Arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $Runner
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -Daily -At "16:30"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Daily Research Report Memory L0/L1 review and L2B Dreaming" -Force | Out-Null
Write-Output "Enabled daily 16:30 Report Memory Dreaming task: $TaskName"
