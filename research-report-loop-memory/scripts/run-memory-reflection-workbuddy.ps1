$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$PluginRoot = Split-Path -Parent $PSScriptRoot
$DataDir = if ($env:RESEARCH_REPORT_MEMORY_V2_0821_DIR) {
    $env:RESEARCH_REPORT_MEMORY_V2_0821_DIR
} else {
    Join-Path $HOME ".research-report-memory-v2-0821"
}
$SettingsFile = Join-Path $DataDir "settings.json"
if (-not (Test-Path $SettingsFile)) {
    Write-Output "Report Memory is disabled; Reflection skipped."
    exit 0
}
try {
    $MemorySettings = Get-Content -Raw -Encoding utf8 $SettingsFile | ConvertFrom-Json
} catch {
    Write-Output "Report Memory settings are invalid; Reflection skipped."
    exit 0
}
if ($MemorySettings.schemaVersion -ne 1 -or $MemorySettings.memoryEnabled -ne $true) {
    Write-Output "Report Memory is disabled; Reflection skipped."
    exit 0
}
$WorkBuddyConfig = if ($env:WORKBUDDY_CONFIG_DIR) {
    $env:WORKBUDDY_CONFIG_DIR
} elseif ($env:CODEBUDDY_CONFIG_DIR) {
    $env:CODEBUDDY_CONFIG_DIR
} else {
    Join-Path $HOME ".workbuddy"
}
$McpName = if ($env:RESEARCH_REPORT_REFLECTION_MCP_NAME) {
    $env:RESEARCH_REPORT_REFLECTION_MCP_NAME
} else {
    "report-memory-v2"
}

$NodeBin = if ($env:WORKBUDDY_NODE) {
    $env:WORKBUDDY_NODE
} elseif ($env:CODEBUDDY_CODE_NODE_PATH) {
    $env:CODEBUDDY_CODE_NODE_PATH
} else {
    $env:CODEBUDDY_NODE_BIN
}
if (-not $NodeBin -and $env:WORKBUDDY_EXTRA_PATHS) {
    foreach ($directory in ($env:WORKBUDDY_EXTRA_PATHS -split ";")) {
        $candidate = Join-Path $directory "node.exe"
        if (Test-Path $candidate) { $NodeBin = $candidate; break }
    }
}
if (-not $NodeBin) {
    $NodeBin = Get-ChildItem (Join-Path $WorkBuddyConfig "binaries\node\versions") -Filter node.exe -Recurse -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $NodeBin) {
    $NodeBin = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
}
if (-not $NodeBin) { throw "Node.js 22.16+ not found; set WORKBUDDY_NODE." }

$WorkBuddyExe = $env:WORKBUDDY_DESKTOP_EXE
$CodeBuddyCli = if ($env:WORKBUDDY_CODEBUDDY) {
    $env:WORKBUDDY_CODEBUDDY
} else {
    $env:CODEBUDDY_CODE_PATH
}
if (-not $WorkBuddyExe) {
    $InstallRoots = @()
    if ($env:LOCALAPPDATA) { $InstallRoots += (Join-Path $env:LOCALAPPDATA "Programs\WorkBuddy") }
    if ($env:ProgramFiles) { $InstallRoots += (Join-Path $env:ProgramFiles "WorkBuddy") }
    if (${env:ProgramFiles(x86)}) { $InstallRoots += (Join-Path ${env:ProgramFiles(x86)} "WorkBuddy") }
    foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        $InstallRoots += (Join-Path $drive.Root "Program Files\WorkBuddy")
    }
    foreach ($root in $InstallRoots) {
        $candidate = Join-Path $root "WorkBuddy.exe"
        if (Test-Path $candidate) { $WorkBuddyExe = $candidate }
        if ($WorkBuddyExe) { break }
    }
}
if (-not $CodeBuddyCli -and $WorkBuddyExe) {
    $root = Split-Path -Parent $WorkBuddyExe
    $candidate = Join-Path $root "resources\app.asar.unpacked\cli\bin\codebuddy"
    if (Test-Path $candidate) { $CodeBuddyCli = $candidate }
}
if (-not $CodeBuddyCli) {
    throw "WorkBuddy is installed, but its model CLI entry could not be located. Check CODEBUDDY_CODE_PATH or set WORKBUDDY_CODEBUDDY."
}

$ProductConfig = $env:WORKBUDDY_PRODUCT_CONFIG
if (-not $ProductConfig -and $CodeBuddyCli) {
    $cliRoot = Split-Path -Parent (Split-Path -Parent $CodeBuddyCli)
    $candidate = Join-Path $cliRoot "product.json"
    if (Test-Path $candidate) { $ProductConfig = $candidate }
}
if (-not $ProductConfig) { throw "WorkBuddy product.json not found; set WORKBUDDY_PRODUCT_CONFIG." }

$ReflectionDir = Join-Path $DataDir "reflection"
$LogDir = Join-Path $DataDir "reflection-logs"
New-Item -ItemType Directory -Force -Path $ReflectionDir, $LogDir | Out-Null
$McpConfig = Join-Path $ReflectionDir "windows-mcp-config.json"
$NodeRunner = if ($env:RESEARCH_REPORT_REFLECTION_NODE_RUNNER) {
    $env:RESEARCH_REPORT_REFLECTION_NODE_RUNNER
} else {
    Join-Path $PluginRoot "scripts\run-node.cmd"
}
$MemoryServer = Join-Path $PluginRoot "dist\memory-server.mjs"
$McpServers = @{}
$McpServers[$McpName] = @{
    command = "cmd.exe"
    args = @("/d", "/c", $NodeRunner, $MemoryServer)
    env = @{ RESEARCH_REPORT_MEMORY_V2_0821_DIR = $DataDir }
}
$Config = @{
    mcpServers = $McpServers
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($McpConfig, ($Config | ConvertTo-Json -Depth 8), $Utf8NoBom)

$Instructions = Get-Content -Raw -Encoding utf8 (Join-Path $PluginRoot "agents\research-report-memory-reflection.md")
$Prompt = "Execute operation=reflection. Review writing activity since the last checkpoint according to the Reflection Prompt. Call Recall exactly once with purpose=reflection. If there is work, call Capture Payload exactly once and submit only incremental changes; otherwise finish without Capture."
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "$Stamp.jsonl"

$env:CODEBUDDY_CONFIG_DIR = $WorkBuddyConfig
$env:WORKBUDDY_CONFIG_DIR = $WorkBuddyConfig
$env:ACC_PRODUCT_CONFIG_PATH = $ProductConfig
$env:RESEARCH_REPORT_MEMORY_V2_0821_DIR = $DataDir
$env:RESEARCH_REPORT_MEMORY_ALLOW_STORAGE_MIGRATION = "0"

$CliHost = if ($WorkBuddyExe) { $WorkBuddyExe } else { $NodeBin }
if ($WorkBuddyExe) { $env:ELECTRON_RUN_AS_NODE = "1" }
& $CliHost $CodeBuddyCli --plugin-dir $PluginRoot -p `
    --mcp-config $McpConfig --strict-mcp-config `
    --output-format stream-json --permission-mode bypassPermissions `
    --append-system-prompt $Instructions $Prompt |
    Out-File -Encoding utf8 $LogFile
if ($LASTEXITCODE -ne 0) { throw "WorkBuddy Reflection exited with code $LASTEXITCODE" }
