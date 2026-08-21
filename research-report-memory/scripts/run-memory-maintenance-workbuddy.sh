#!/bin/sh
set -eu

PLUGIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CODEBUDDY_BIN=${WORKBUDDY_CODEBUDDY:-}
WORKBUDDY_USER_DIR=${CODEBUDDY_CONFIG_DIR:-${WORKBUDDY_CONFIG_DIR:-$HOME/.workbuddy}}
PRODUCT_CONFIG=${WORKBUDDY_PRODUCT_CONFIG:-}
DATA_DIR=${RESEARCH_REPORT_MEMORY_V2_DIR:-${RESEARCH_REPORT_MEMORY_DIR:-$HOME/.research-report-memory-v2-mvp}}
NODE_BIN=${WORKBUDDY_NODE:-}

if [ -z "$NODE_BIN" ]; then
  for candidate in "$HOME"/.workbuddy/binaries/node/versions/*/bin/node; do
    if [ -x "$candidate" ]; then NODE_BIN=$candidate; fi
  done
fi
if [ -z "$NODE_BIN" ] && command -v node >/dev/null 2>&1; then
  NODE_BIN=$(command -v node)
fi
if [ -z "$NODE_BIN" ] || [ ! -x "$NODE_BIN" ]; then
  echo "未找到 WorkBuddy Node.js；请设置 WORKBUDDY_NODE。" >&2
  exit 1
fi
PATH=$(dirname "$NODE_BIN"):$PATH
export PATH

if [ -z "$CODEBUDDY_BIN" ] && command -v codebuddy >/dev/null 2>&1; then
  CODEBUDDY_BIN=$(command -v codebuddy)
fi
if [ -z "$CODEBUDDY_BIN" ] && [ -x /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy ]; then
  CODEBUDDY_BIN=/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
fi
if [ -z "$CODEBUDDY_BIN" ] || [ ! -x "$CODEBUDDY_BIN" ]; then
  echo "未找到 WorkBuddy CLI；请设置 WORKBUDDY_CODEBUDDY。" >&2
  exit 1
fi
if [ -z "$PRODUCT_CONFIG" ]; then
  candidate=$(CDPATH= cd -- "$(dirname -- "$CODEBUDDY_BIN")/.." && pwd)/product.json
  if [ -f "$candidate" ]; then PRODUCT_CONFIG=$candidate; fi
fi
if [ -z "$PRODUCT_CONFIG" ] || [ ! -f "$PRODUCT_CONFIG" ]; then
  echo "未找到 WorkBuddy product.json；请设置 WORKBUDDY_PRODUCT_CONFIG。" >&2
  exit 1
fi

mkdir -p "$DATA_DIR/maintenance" "$DATA_DIR/maintenance-logs"
STAMP=$(date '+%Y%m%d-%H%M%S')
INSTRUCTIONS=$(sed '/^---$/,/^---$/d' "$PLUGIN_ROOT/agents/research-report-memory-curator.md")
MCP_CONFIG_FILE="$DATA_DIR/maintenance/v2-mcp-config.json"

"$NODE_BIN" -e '
const fs = require("node:fs");
const path = require("node:path");
const [outputPath, pluginRoot, dataDir] = process.argv.slice(1);
const bundledServer = path.join(pluginRoot, "dist/server.mjs");
const sourceServer = path.join(pluginRoot, "mcp/src/server.ts");
const args = fs.existsSync(bundledServer)
  ? [bundledServer]
  : ["--import", "tsx", sourceServer];
const config = {
  mcpServers: {
    "research-report-memory-v2-mvp": {
      command: "sh",
      args: [path.join(pluginRoot, "scripts/run-node.sh"), ...args],
      env: { RESEARCH_REPORT_MEMORY_V2_DIR: dataDir },
    },
  },
};
const tempPath = `${outputPath}.tmp`;
fs.writeFileSync(tempPath, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
fs.renameSync(tempPath, outputPath);
' "$MCP_CONFIG_FILE" "$PLUGIN_ROOT" "$DATA_DIR"

env CODEBUDDY_CONFIG_DIR="$WORKBUDDY_USER_DIR" \
  ACC_PRODUCT_CONFIG_PATH="$PRODUCT_CONFIG" \
  RESEARCH_REPORT_MEMORY_V2_DIR="$DATA_DIR" \
  RESEARCH_REPORT_MEMORY_ALLOW_STORAGE_MIGRATION=0 \
  "$CODEBUDDY_BIN" --plugin-dir "$PLUGIN_ROOT" -p \
  --mcp-config "$MCP_CONFIG_FILE" \
  --strict-mcp-config \
  --output-format stream-json \
  --permission-mode bypassPermissions \
  --append-system-prompt "$INSTRUCTIONS" \
  "执行 operation=maintenance：只调用 mcp__research-report-memory-v2-mvp__writing_memory_recall 和 mcp__research-report-memory-v2-mvp__writing_memory_capture_payload，不得调用无 v2-mvp 后缀的旧版 research-report-memory MCP。先读取 purpose=maintenance 的 Snapshot，完成 pending L0→L1 提炼，并按 Layer 判定规则与 Scope 判定规则复查现有 L1/L2/L3，处理 Layer 晋升过早或滞后、Scope 误判、冲突、过期和上下文膨胀。没有待办时直接结束。" \
  > "$DATA_DIR/maintenance-logs/$STAMP.jsonl"
