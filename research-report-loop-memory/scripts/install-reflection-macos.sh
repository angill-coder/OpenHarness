#!/bin/sh
set -eu

PLUGIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LABEL=com.research-report.loop-memory.reflection
SCHEDULE_FILE="$HOME/Library/LaunchAgents/$LABEL.plist"
LEGACY_SCHEDULE_FILE="$HOME/Library/LaunchAgents/com.research-report.loop-memory.maintenance.plist"
LOG_DIR="$HOME/.research-report-memory-v2-0821/reflection-logs"
REFLECTION_DIR="${RESEARCH_REPORT_MEMORY_V2_0821_DIR:-$HOME/.research-report-memory-v2-0821}/reflection"
STABLE_RUNNER="$REFLECTION_DIR/reflection-current.sh"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR" "$REFLECTION_DIR"
cp "$PLUGIN_ROOT/scripts/reflection-current.sh" "$STABLE_RUNNER"
chmod 700 "$STABLE_RUNNER"
if [ -f "$LEGACY_SCHEDULE_FILE" ]; then
  launchctl bootout "gui/$(id -u)" "$LEGACY_SCHEDULE_FILE" >/dev/null 2>&1 || true
  rm -f "$LEGACY_SCHEDULE_FILE"
fi
sed \
  -e "s|__LABEL__|$LABEL|g" \
  -e "s|__RUNNER__|$STABLE_RUNNER|g" \
  -e "s|__STDOUT__|$LOG_DIR/reflection-schedule.stdout.log|g" \
  -e "s|__STDERR__|$LOG_DIR/reflection-schedule.stderr.log|g" \
  "$PLUGIN_ROOT/scripts/macos-reflection-schedule.plist.template" > "$SCHEDULE_FILE"

launchctl bootout "gui/$(id -u)" "$SCHEDULE_FILE" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$SCHEDULE_FILE"
echo "已启用每天 16:30 的 Report Memory Reflection 定时任务：$SCHEDULE_FILE"
