#!/bin/sh
set -eu

LABEL=com.research-report.loop-memory.reflection
SCHEDULE_FILE="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA_DIR="${RESEARCH_REPORT_MEMORY_V2_0821_DIR:-$HOME/.research-report-memory-v2-0821}"
REFLECTION_DIR="$DATA_DIR/reflection"
SETTINGS_FILE="$REFLECTION_DIR/schedule-settings.json"

launchctl bootout "gui/$(id -u)" "$SCHEDULE_FILE" >/dev/null 2>&1 || true
rm -f "$SCHEDULE_FILE"
mkdir -p "$REFLECTION_DIR"
UPDATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"enabled":false,"updatedAt":"%s"}\n' "$UPDATED_AT" > "$SETTINGS_FILE.tmp"
mv "$SETTINGS_FILE.tmp" "$SETTINGS_FILE"
echo "已关闭 Report Memory Reflection 定时任务。"
