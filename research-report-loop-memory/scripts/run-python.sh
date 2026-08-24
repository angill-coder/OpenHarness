#!/bin/sh
set -eu

PYTHON_BIN=${WORKBUDDY_PYTHON:-}

if [ -z "$PYTHON_BIN" ]; then
  for candidate in "$HOME"/.workbuddy/binaries/python/versions/*/bin/python3; do
    if [ -x "$candidate" ]; then PYTHON_BIN=$candidate; fi
  done
fi

if [ -z "$PYTHON_BIN" ] && command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=$(command -v python3)
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "research-report-loop requires Python 3.10 or newer" >&2
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "research-report-loop requires Python 3.10 or newer" >&2
  exit 1
fi

exec "$PYTHON_BIN" "$@"
