#!/bin/sh
set -eu

PLUGIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CODEBUDDY_BIN=${RESEARCH_REPORT_LOOP_WB_CLI_PATH:-${WORKBUDDY_CLI:-${WORKBUDDY_CODEBUDDY:-${CODEBUDDY_CODE_PATH:-}}}}
WORKBUDDY_USER_DIR=${WORKBUDDY_CONFIG_DIR:-${CODEBUDDY_CONFIG_DIR:-$HOME/.workbuddy}}
PRODUCT_CONFIG=${WORKBUDDY_PRODUCT_CONFIG:-}
DATA_DIR=${RESEARCH_REPORT_MEMORY_V2_0821_DIR:-$HOME/.research-report-memory-v2-0821}
NODE_BIN=${WORKBUDDY_NODE:-${CODEBUDDY_CODE_NODE_PATH:-${CODEBUDDY_NODE_BIN:-}}}
MCP_NAME=${RESEARCH_REPORT_REFLECTION_MCP_NAME:-report-memory-v2}
NODE_RUNNER=${RESEARCH_REPORT_REFLECTION_NODE_RUNNER:-$PLUGIN_ROOT/scripts/run-node.sh}
SETTINGS_FILE="$DATA_DIR/settings.json"

if [ ! -f "$SETTINGS_FILE" ] || ! grep -Eq '"memoryEnabled"[[:space:]]*:[[:space:]]*true' "$SETTINGS_FILE"; then
  echo "Report Memory is disabled; Reflection skipped."
  exit 0
fi

if [ -z "$NODE_BIN" ]; then
  old_ifs=$IFS
  IFS=:
  for directory in ${WORKBUDDY_EXTRA_PATHS:-}; do
    if [ -x "$directory/node" ]; then NODE_BIN=$directory/node; break; fi
  done
  IFS=$old_ifs
fi
if [ -z "$NODE_BIN" ]; then
  for candidate in "$WORKBUDDY_USER_DIR"/binaries/node/versions/*/bin/node; do
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

if [ -z "$CODEBUDDY_BIN" ]; then
  for name in workbuddy codebuddy cbc; do
    if command -v "$name" >/dev/null 2>&1; then CODEBUDDY_BIN=$(command -v "$name"); break; fi
  done
fi
if [ -z "$CODEBUDDY_BIN" ]; then
  for candidate in \
    /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy \
    "$HOME"/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy \
    /Volumes/*/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy; do
    if [ -x "$candidate" ]; then CODEBUDDY_BIN=$candidate; break; fi
  done
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

mkdir -p "$DATA_DIR/reflection" "$DATA_DIR/reflection-logs"
STAMP=$(date '+%Y%m%d-%H%M%S')
INSTRUCTIONS=$(sed '/^---$/,/^---$/d' "$PLUGIN_ROOT/agents/research-report-memory-reflection.md")
MCP_CONFIG_FILE="$DATA_DIR/reflection/integrated-mcp-config.json"

"$NODE_BIN" -e '
const fs = require("node:fs");
const path = require("node:path");
const [outputPath, pluginRoot, dataDir, nodeRunner, mcpName] = process.argv.slice(1);
const bundledServer = path.join(pluginRoot, "dist/memory-server.mjs");
const sourceServer = path.join(pluginRoot, "mcp/src/server.ts");
const args = fs.existsSync(bundledServer)
  ? [bundledServer]
  : ["--import", "tsx", sourceServer];
const config = {
  mcpServers: {
    [mcpName]: {
      command: "sh",
      args: [nodeRunner, ...args],
      env: { RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDir },
    },
  },
};
const temporary = `${outputPath}.tmp`;
fs.writeFileSync(temporary, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
fs.renameSync(temporary, outputPath);
' "$MCP_CONFIG_FILE" "$PLUGIN_ROOT" "$DATA_DIR" "$NODE_RUNNER" "$MCP_NAME"

env WORKBUDDY_CONFIG_DIR="$WORKBUDDY_USER_DIR" \
  CODEBUDDY_CONFIG_DIR="$WORKBUDDY_USER_DIR" \
  ACC_PRODUCT_CONFIG_PATH="$PRODUCT_CONFIG" \
  RESEARCH_REPORT_MEMORY_V2_0821_DIR="$DATA_DIR" \
  RESEARCH_REPORT_MEMORY_ALLOW_STORAGE_MIGRATION=0 \
  "$CODEBUDDY_BIN" --plugin-dir "$PLUGIN_ROOT" -p \
  --mcp-config "$MCP_CONFIG_FILE" \
  --strict-mcp-config \
  --output-format stream-json \
  --permission-mode bypassPermissions \
  --append-system-prompt "$INSTRUCTIONS" \
  "执行 operation=reflection：按 Reflection Prompt 审视上次 checkpoint 后的写作经历。只调用一次 purpose=reflection 的 Recall；有工作时只调用一次 Capture Payload，且只提交增量修改；没有工作时直接结束。" \
  > "$DATA_DIR/reflection-logs/$STAMP.jsonl"
