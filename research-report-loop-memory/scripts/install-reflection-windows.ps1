$ErrorActionPreference = "Stop"
$TaskName = "ResearchReportLoopMemoryReflection"
$Runner = Join-Path $PSScriptRoot "run-memory-reflection-workbuddy.ps1"
if (-not (Test-Path $Runner)) { throw "Reflection runner not found: $Runner" }

$Arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $Runner
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -Daily -At "16:30"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Daily Research Report Memory Reflection" -Force | Out-Null
Write-Output "Enabled daily 16:30 Report Memory Reflection task: $TaskName"
