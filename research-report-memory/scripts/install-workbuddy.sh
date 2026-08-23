#!/bin/sh
set -eu

PLUGIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
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
  echo "需要 Node.js >=22.16。请设置 WORKBUDDY_NODE 后重试。" >&2
  exit 1
fi

if ! "$NODE_BIN" -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22 || (a===22 && b>=16) ? 0 : 1)'; then
  echo "当前 Node.js 版本过低；需要 >=22.16。请设置 WORKBUDDY_NODE 后重试。" >&2
  exit 1
fi

NPM_BIN=$(dirname "$NODE_BIN")/npm

if [ ! -d "$PLUGIN_ROOT/node_modules" ]; then
  "$NPM_BIN" install --omit=dev --omit=optional --prefix "$PLUGIN_ROOT"
fi

echo "依赖已就绪。请在 WorkBuddy 中用本地插件目录加载："
echo "  $PLUGIN_ROOT"
echo "开发测试命令：codebuddy --plugin-dir \"$PLUGIN_ROOT\""
