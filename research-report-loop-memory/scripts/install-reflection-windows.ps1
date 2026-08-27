$ErrorActionPreference = "Stop"
$TaskName = "ResearchReportLoopMemoryReflection"
$DataDir = if ($env:RESEARCH_REPORT_MEMORY_V2_0821_DIR) {
    $env:RESEARCH_REPORT_MEMORY_V2_0821_DIR
} else {
    Join-Path $HOME ".research-report-memory-v2-0821"
}
$ReflectionDir = Join-Path $DataDir "reflection"
$StableRunner = Join-Path $ReflectionDir "reflection-current.ps1"
$SettingsFile = Join-Path $ReflectionDir "schedule-settings.json"
$RunnerSource = Join-Path $PSScriptRoot "reflection-current.ps1"
if (-not (Test-Path $RunnerSource)) { throw "Stable Reflection launcher not found: $RunnerSource" }
New-Item -ItemType Directory -Force -Path $ReflectionDir | Out-Null
Copy-Item -Force $RunnerSource $StableRunner

$Arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $StableRunner
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -Daily -At "16:30"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Daily Research Report Memory Reflection" -Force | Out-Null
$Preference = @{ enabled = $true; updatedAt = (Get-Date).ToUniversalTime().ToString("o") } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($SettingsFile, $Preference, [System.Text.UTF8Encoding]::new($false))
Write-Output "Enabled daily 16:30 Report Memory Reflection task: $TaskName"
