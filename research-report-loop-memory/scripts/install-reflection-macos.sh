#!/bin/sh
set -eu

PLUGIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LABEL=com.research-report.loop-memory.reflection
SCHEDULE_FILE="$HOME/Library/LaunchAgents/$LABEL.plist"
LEGACY_SCHEDULE_FILE="$HOME/Library/LaunchAgents/com.research-report.loop-memory.maintenance.plist"
DATA_DIR="${RESEARCH_REPORT_MEMORY_V2_0821_DIR:-$HOME/.research-report-memory-v2-0821}"
LOG_DIR="$DATA_DIR/reflection-logs"
REFLECTION_DIR="$DATA_DIR/reflection"
STABLE_RUNNER="$REFLECTION_DIR/reflection-current.sh"
SETTINGS_FILE="$REFLECTION_DIR/schedule-settings.json"
WORKBUDDY_USER_DIR=${WORKBUDDY_CONFIG_DIR:-${CODEBUDDY_CONFIG_DIR:-$HOME/.workbuddy}}
CODEBUDDY_BIN=${RESEARCH_REPORT_LOOP_WB_CLI_PATH:-${WORKBUDDY_CLI:-${WORKBUDDY_CODEBUDDY:-${CODEBUDDY_CODE_PATH:-}}}}
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
  for candidate in "$WORKBUDDY_USER_DIR"/binaries/node/versions/*/bin/node; do
    if [ -x "$candidate" ]; then NODE_BIN=$candidate; fi
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

plutil -insert EnvironmentVariables -json '{}' "$SCHEDULE_FILE"
plutil -insert EnvironmentVariables.WORKBUDDY_CONFIG_DIR -string "$WORKBUDDY_USER_DIR" "$SCHEDULE_FILE"
plutil -insert EnvironmentVariables.CODEBUDDY_CONFIG_DIR -string "$WORKBUDDY_USER_DIR" "$SCHEDULE_FILE"
if [ -n "$NODE_BIN" ]; then
  plutil -insert EnvironmentVariables.CODEBUDDY_NODE_BIN -string "$NODE_BIN" "$SCHEDULE_FILE"
fi
if [ -n "$CODEBUDDY_BIN" ]; then
  plutil -insert EnvironmentVariables.WORKBUDDY_CODEBUDDY -string "$CODEBUDDY_BIN" "$SCHEDULE_FILE"
fi
if [ -n "${WORKBUDDY_EXTRA_PATHS:-}" ]; then
  plutil -insert EnvironmentVariables.WORKBUDDY_EXTRA_PATHS -string "$WORKBUDDY_EXTRA_PATHS" "$SCHEDULE_FILE"
fi

launchctl bootout "gui/$(id -u)" "$SCHEDULE_FILE" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$SCHEDULE_FILE"
UPDATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"enabled":true,"updatedAt":"%s"}\n' "$UPDATED_AT" > "$SETTINGS_FILE.tmp"
mv "$SETTINGS_FILE.tmp" "$SETTINGS_FILE"
echo "已启用每天 16:30 的 Report Memory Reflection 定时任务：$SCHEDULE_FILE"
