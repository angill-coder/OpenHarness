$ErrorActionPreference = "Stop"

$WorkBuddyConfig = if ($env:CODEBUDDY_CONFIG_DIR) {
    $env:CODEBUDDY_CONFIG_DIR
} else {
    Join-Path $HOME ".workbuddy"
}
$RegistryPath = Join-Path $WorkBuddyConfig "plugins\installed_plugins.json"
if (-not (Test-Path $RegistryPath)) {
    throw "WorkBuddy plugin registry not found: $RegistryPath"
}

$Registry = Get-Content -Raw -Encoding utf8 $RegistryPath | ConvertFrom-Json
$ExactKey = "research-report-loop-memory@research-report-loop-memory-local"
$Entry = $Registry.$ExactKey
if (-not $Entry) {
    $Fallback = $Registry.PSObject.Properties |
        Where-Object { $_.Name -like "research-report-loop-memory@*" } |
        Select-Object -First 1
    if ($Fallback) { $Entry = $Fallback.Value }
}
if ($Entry -is [System.Array]) { $Entry = $Entry | Select-Object -First 1 }
$PluginRoot = $Entry.installPath
if (-not $PluginRoot) {
    throw "Installed research-report-loop-memory plugin not found in: $RegistryPath"
}

$Runner = Join-Path $PluginRoot "scripts\run-memory-reflection-workbuddy.ps1"
if (-not (Test-Path $Runner)) {
    throw "Current Reflection runner not found: $Runner"
}
& $Runner
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
