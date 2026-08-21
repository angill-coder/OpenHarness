#!/bin/sh
set -eu

PLUGIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LABEL=com.research-report.memory-v2-mvp.maintenance
AGENT_FILE="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.research-report-memory-v2-mvp/maintenance-logs"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
sed \
  -e "s|__LABEL__|$LABEL|g" \
  -e "s|__RUNNER__|$PLUGIN_ROOT/scripts/run-memory-maintenance-workbuddy.sh|g" \
  -e "s|__STDOUT__|$LOG_DIR/launchd.stdout.log|g" \
  -e "s|__STDERR__|$LOG_DIR/launchd.stderr.log|g" \
  "$PLUGIN_ROOT/scripts/maintenance-launchagent.plist.template" > "$AGENT_FILE"

launchctl bootout "gui/$(id -u)" "$AGENT_FILE" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$AGENT_FILE"
echo "已启用每天 16:30 的 V2 MVP Memory Agent 整理：$AGENT_FILE"
