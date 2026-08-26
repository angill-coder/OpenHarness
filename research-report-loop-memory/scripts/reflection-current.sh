#!/bin/sh
set -eu

WORKBUDDY_DIR=${WORKBUDDY_CONFIG_DIR:-${CODEBUDDY_CONFIG_DIR:-$HOME/.workbuddy}}
INSTALLED_PLUGINS="$WORKBUDDY_DIR/plugins/installed_plugins.json"
NODE_BIN=${WORKBUDDY_NODE:-${CODEBUDDY_CODE_NODE_PATH:-${CODEBUDDY_NODE_BIN:-}}}

if [ -z "$NODE_BIN" ]; then
  old_ifs=$IFS
  IFS=:
  for directory in ${WORKBUDDY_EXTRA_PATHS:-}; do
    if [ -x "$directory/node" ]; then NODE_BIN=$directory/node; break; fi
  done
  IFS=$old_ifs
fi
if [ -z "$NODE_BIN" ]; then
  for candidate in "$WORKBUDDY_DIR"/binaries/node/versions/*/bin/node; do
    if [ -x "$candidate" ]; then NODE_BIN=$candidate; fi
  done
fi
if [ -z "$NODE_BIN" ] && command -v node >/dev/null 2>&1; then
  NODE_BIN=$(command -v node)
fi
if [ -z "$NODE_BIN" ] || [ ! -x "$NODE_BIN" ]; then
  echo "未找到 WorkBuddy Node.js；无法解析当前 Reflection 插件版本。" >&2
  exit 1
fi
if [ ! -f "$INSTALLED_PLUGINS" ]; then
  echo "未找到 WorkBuddy 插件注册表：$INSTALLED_PLUGINS" >&2
  exit 1
fi

PLUGIN_ROOT=$(
  "$NODE_BIN" -e '
const fs = require("node:fs");
const registry = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const plugins = registry.plugins && typeof registry.plugins === "object" ? registry.plugins : registry;
const exact = plugins["research-report-loop-memory@research-report-loop-memory-local"];
const fallback = Object.entries(plugins).find(([key]) => key.startsWith("research-report-loop-memory@"))?.[1];
const entries = Array.isArray(exact) ? exact : Array.isArray(fallback) ? fallback : [];
const active = entries.find((entry) => entry && typeof entry.installPath === "string");
if (!active) process.exit(2);
process.stdout.write(active.installPath);
' "$INSTALLED_PLUGINS"
)

RUNNER="$PLUGIN_ROOT/scripts/run-memory-reflection-workbuddy.sh"
if [ ! -f "$RUNNER" ]; then
  echo "当前插件缺少 Reflection Runner：$RUNNER" >&2
  exit 1
fi

exec /bin/sh "$RUNNER"
