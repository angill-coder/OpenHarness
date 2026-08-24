$ErrorActionPreference = "Stop"
$PluginRoot = Split-Path -Parent $PSScriptRoot
$DataDir = if ($env:RESEARCH_REPORT_MEMORY_V2_0821_DIR) {
    $env:RESEARCH_REPORT_MEMORY_V2_0821_DIR
} else {
    Join-Path $HOME ".research-report-memory-v2-0821"
}
$WorkBuddyConfig = if ($env:CODEBUDDY_CONFIG_DIR) {
    $env:CODEBUDDY_CONFIG_DIR
} else {
    Join-Path $HOME ".workbuddy"
}

$NodeBin = $env:WORKBUDDY_NODE
if (-not $NodeBin) {
    $NodeBin = Get-ChildItem (Join-Path $HOME ".workbuddy\binaries\node\versions") -Filter node.exe -Recurse -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $NodeBin) {
    $NodeBin = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
}
if (-not $NodeBin) { throw "Node.js 22.16+ not found; set WORKBUDDY_NODE." }

$WorkBuddyExe = $env:WORKBUDDY_DESKTOP_EXE
$CodeBuddyCli = $env:WORKBUDDY_CODEBUDDY
if (-not $WorkBuddyExe) {
    if ($env:LOCALAPPDATA) {
        $candidate = Join-Path $env:LOCALAPPDATA "Programs\WorkBuddy\WorkBuddy.exe"
        if (Test-Path $candidate) { $WorkBuddyExe = $candidate }
    }
}
if (-not $CodeBuddyCli -and $WorkBuddyExe) {
    $root = Split-Path -Parent $WorkBuddyExe
    $candidate = Join-Path $root "resources\app.asar.unpacked\cli\bin\codebuddy"
    if (Test-Path $candidate) { $CodeBuddyCli = $candidate }
}
if (-not $WorkBuddyExe -or -not $CodeBuddyCli) {
    throw "WorkBuddy CLI not found; set WORKBUDDY_DESKTOP_EXE and WORKBUDDY_CODEBUDDY."
}

$ProductConfig = $env:WORKBUDDY_PRODUCT_CONFIG
if (-not $ProductConfig) {
    $root = Split-Path -Parent $WorkBuddyExe
    $candidate = Join-Path $root "resources\app.asar.unpacked\cli\product.json"
    if (Test-Path $candidate) { $ProductConfig = $candidate }
}
if (-not $ProductConfig) { throw "WorkBuddy product.json not found; set WORKBUDDY_PRODUCT_CONFIG." }

$MaintenanceDir = Join-Path $DataDir "maintenance"
$LogDir = Join-Path $DataDir "maintenance-logs"
New-Item -ItemType Directory -Force -Path $MaintenanceDir, $LogDir | Out-Null
$McpConfig = Join-Path $MaintenanceDir "windows-mcp-config.json"
$NodeRunner = Join-Path $PluginRoot "scripts\run-node.cmd"
$MemoryServer = Join-Path $PluginRoot "dist\memory-server.mjs"
$CommandLine = '""{0}" "{1}""' -f $NodeRunner, $MemoryServer
$Config = @{
    mcpServers = @{
        "research-report-memory-v2-0821" = @{
            command = "cmd.exe"
            args = @("/d", "/s", "/c", $CommandLine)
            env = @{ RESEARCH_REPORT_MEMORY_V2_0821_DIR = $DataDir }
        }
    }
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($McpConfig, ($Config | ConvertTo-Json -Depth 8), $Utf8NoBom)

$Instructions = Get-Content -Raw -Encoding utf8 (Join-Path $PluginRoot "agents\research-report-memory-curator.md")
$Prompt = "执行 operation=maintenance：只调用 research-report-memory-v2-0821 的 recall 和 capture_payload。读取 purpose=maintenance 的 Snapshot，只处理 pending、dirty 和疑似冲突，只提交增量修改；普通单次反馈不得自动晋升 L2B。没有待办时直接结束。"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "$Stamp.jsonl"

$env:ELECTRON_RUN_AS_NODE = "1"
$env:CODEBUDDY_CONFIG_DIR = $WorkBuddyConfig
$env:ACC_PRODUCT_CONFIG_PATH = $ProductConfig
$env:RESEARCH_REPORT_MEMORY_V2_0821_DIR = $DataDir
$env:RESEARCH_REPORT_MEMORY_ALLOW_STORAGE_MIGRATION = "0"

& $WorkBuddyExe $CodeBuddyCli --plugin-dir $PluginRoot -p `
    --mcp-config $McpConfig --strict-mcp-config `
    --output-format stream-json --permission-mode bypassPermissions `
    --append-system-prompt $Instructions $Prompt |
    Out-File -Encoding utf8 $LogFile
if ($LASTEXITCODE -ne 0) { throw "WorkBuddy maintenance exited with code $LASTEXITCODE" }
