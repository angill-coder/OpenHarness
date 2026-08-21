#!/bin/sh
set -eu

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
  echo "research-report-memory requires Node.js >=22.16" >&2
  exit 1
fi

if ! "$NODE_BIN" -e 'const [a,b]=process.versions.node.split(".").map(Number); process.exit(a>22 || (a===22 && b>=16) ? 0 : 1)'; then
  echo "research-report-memory requires Node.js >=22.16; set WORKBUDDY_NODE to a compatible binary" >&2
  exit 1
fi

exec "$NODE_BIN" "$@"
