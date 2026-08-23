#!/bin/sh
set -eu

PLUGIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MARKETPLACE_NAME=research-report-memory-v2-mvp-local
PLUGIN_REF="research-report-memory-v2-mvp@$MARKETPLACE_NAME"
CODEBUDDY_BIN=${WORKBUDDY_CODEBUDDY:-}
WORKBUDDY_USER_DIR=${CODEBUDDY_CONFIG_DIR:-${WORKBUDDY_CONFIG_DIR:-$HOME/.workbuddy}}
PRODUCT_CONFIG=${WORKBUDDY_PRODUCT_CONFIG:-}

if [ -z "$CODEBUDDY_BIN" ] && command -v codebuddy >/dev/null 2>&1; then
  CODEBUDDY_BIN=$(command -v codebuddy)
fi
if [ -z "$CODEBUDDY_BIN" ] && [ -x /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy ]; then
  CODEBUDDY_BIN=/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
fi
if [ -z "$CODEBUDDY_BIN" ] || [ ! -x "$CODEBUDDY_BIN" ]; then
  echo "未找到 WorkBuddy CLI；可通过 WORKBUDDY_CODEBUDDY 指定路径。" >&2
  exit 1
fi
if [ -z "$PRODUCT_CONFIG" ]; then
  candidate=$(CDPATH= cd -- "$(dirname -- "$CODEBUDDY_BIN")/.." && pwd)/product.json
  if [ -f "$candidate" ]; then PRODUCT_CONFIG=$candidate; fi
fi
if [ -z "$PRODUCT_CONFIG" ] || [ ! -f "$PRODUCT_CONFIG" ]; then
  echo "未找到 WorkBuddy product.json；可通过 WORKBUDDY_PRODUCT_CONFIG 指定路径。" >&2
  exit 1
fi

SOURCE_PLUGIN_DIR="$PLUGIN_ROOT/plugins/research-report-memory-v2-mvp"
PLUGIN_MANIFEST="$SOURCE_PLUGIN_DIR/.codebuddy-plugin/plugin.json"
if [ ! -f "$PLUGIN_MANIFEST" ]; then
  echo "无效安装包：缺少 $PLUGIN_MANIFEST" >&2
  exit 1
fi
VERSION=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$PLUGIN_MANIFEST" | head -n 1)
if [ -z "$VERSION" ]; then
  echo "无效安装包：无法读取插件版本。" >&2
  exit 1
fi

# 本地 directory marketplace 不会被 WorkBuddy 复制到 installed cache。
# 先复制到用户配置中的稳定版本目录，避免用户删除下载包后 Hook/MCP 失效。
INSTALL_ROOT="$WORKBUDDY_USER_DIR/plugins/local-marketplaces/research-report-memory-v2-mvp/$VERSION"
mkdir -p "$INSTALL_ROOT"
cp -R "$PLUGIN_ROOT/." "$INSTALL_ROOT/"
MARKETPLACE_ROOT="$INSTALL_ROOT"
INSTALLED_PLUGIN_DIR="$MARKETPLACE_ROOT/plugins/research-report-memory-v2-mvp"

echo "本地注册插件、Agent、Hook 与 MCP: $PLUGIN_REF"
# WorkBuddy 2.115 对 directory marketplace 的 plugin install 可能只写
# enabledPlugins 而不写 installed_plugins，且 CLI 启动时可能访问远端配置。
# 正式包直接、原子地写入本地注册表，避免假成功和不必要的网络依赖。
sh "$INSTALLED_PLUGIN_DIR/scripts/run-node.sh" \
  "$INSTALLED_PLUGIN_DIR/scripts/register-workbuddy-local.mjs" \
  "$WORKBUDDY_USER_DIR" "$PLUGIN_REF" "$INSTALLED_PLUGIN_DIR" "$VERSION" \
  "$MARKETPLACE_NAME" "$MARKETPLACE_ROOT"

if ! grep -q "\"$PLUGIN_REF\"" "$WORKBUDDY_USER_DIR/plugins/installed_plugins.json"; then
  echo "安装失败：WorkBuddy installed_plugins 未注册 $PLUGIN_REF。" >&2
  exit 1
fi

if [ "$(uname -s)" = "Darwin" ]; then
  echo "启用每天 16:30 的 Memory Agent 整理任务"
  WORKBUDDY_CODEBUDDY="$CODEBUDDY_BIN" \
    WORKBUDDY_PRODUCT_CONFIG="$PRODUCT_CONFIG" \
    CODEBUDDY_CONFIG_DIR="$WORKBUDDY_USER_DIR" \
    sh "$INSTALLED_PLUGIN_DIR/scripts/install-maintenance-macos.sh"
fi

echo "安装完成，Skill/Hook、user-scope MCP 与每日 16:30 Memory Agent 整理任务均已注册。请运行 /reload-plugins，或完全退出并重新启动 WorkBuddy。"
echo "WorkBuddy 配置目录：$WORKBUDDY_USER_DIR"
echo "WorkBuddy 产品配置：$PRODUCT_CONFIG"
echo "稳定安装目录：$INSTALL_ROOT"
echo "V2 MVP user-scope MCP 已自动更新；v0.2.2 和 v1 插件、MCP 与数据未改动。"
