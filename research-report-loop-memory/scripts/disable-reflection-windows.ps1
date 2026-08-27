$ErrorActionPreference = "Stop"
$TaskName = "ResearchReportLoopMemoryReflection"
$DataDir = if ($env:RESEARCH_REPORT_MEMORY_V2_0821_DIR) {
    $env:RESEARCH_REPORT_MEMORY_V2_0821_DIR
} else {
    Join-Path $HOME ".research-report-memory-v2-0821"
}
$ReflectionDir = Join-Path $DataDir "reflection"
$SettingsFile = Join-Path $ReflectionDir "schedule-settings.json"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $ReflectionDir | Out-Null
$Preference = @{ enabled = $false; updatedAt = (Get-Date).ToUniversalTime().ToString("o") } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($SettingsFile, $Preference, [System.Text.UTF8Encoding]::new($false))
Write-Output "Disabled daily Report Memory Reflection task: $TaskName"
