#!/bin/sh
# Install the report-agent-v3 research report team into the local WorkBuddy my-experts
# marketplace so it can be summoned from the WorkBuddy UI.
#
# Usage:
#   npm run install:local          # build, then install
#   sh scripts/install-local-expert.sh --skip-build
#
# What it touches (all under the WorkBuddy config dir):
#   plugins/marketplaces/my-experts/plugins/<name>/        the expert package
#   plugins/marketplaces/my-experts/.codebuddy-plugin/     marketplace index
#   experts/custom/<uuid>/experts.json                     visibility list
#
# It never removes an unrelated expert; the previous copy of THIS expert — and
# directories left by its earlier names — are replaced. The memory directory
# (~/ReportAgentMemory) is left untouched, so reinstalling keeps rubrics and
# episodes.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SKIP_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

WB_DIR=${WORKBUDDY_CONFIG_DIR:-${CODEBUDDY_CONFIG_DIR:-$HOME/.workbuddy}}
if [ ! -d "$WB_DIR" ]; then
  echo "WorkBuddy config directory not found: $WB_DIR" >&2
  echo "Set WORKBUDDY_CONFIG_DIR if it lives elsewhere." >&2
  exit 1
fi

NAME=$(node -p "require('$ROOT/.codebuddy-plugin/plugin.json').name")
VERSION=$(node -p "require('$ROOT/.codebuddy-plugin/plugin.json').version")
PACKAGE_DIR="$ROOT/release/$NAME-$VERSION"

if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "Building $NAME-$VERSION ..."
  (cd "$ROOT" && npm run build:team >/dev/null)
fi
if [ ! -d "$PACKAGE_DIR" ]; then
  echo "Built package not found: $PACKAGE_DIR" >&2
  echo "Run without --skip-build, or run: npm run build:team" >&2
  exit 1
fi

MARKETPLACE="$WB_DIR/plugins/marketplaces/my-experts"
TARGET="$MARKETPLACE/plugins/$NAME"

# Replace this expert's directory atomically enough to avoid a half-copied
# state being picked up by a running WorkBuddy.
mkdir -p "$MARKETPLACE/plugins"
STAGING="$TARGET.installing.$$"
rm -rf "$STAGING"
cp -R "$PACKAGE_DIR" "$STAGING"

# WorkBuddy identifies expert packages in this marketplace by manifest.yaml.
cat > "$STAGING/manifest.yaml" <<YAML
name: $NAME
version: "1"
type: expert
YAML

rm -rf "$TARGET"
mv "$STAGING" "$TARGET"

# Retire directories left by earlier names of THIS expert, so the marketplace
# does not list the same team twice after a rename. Other experts are untouched.
for legacy in research-report-team report-loop; do
  [ "$legacy" = "$NAME" ] && continue
  if [ -d "$MARKETPLACE/plugins/$legacy" ]; then
    rm -rf "$MARKETPLACE/plugins/$legacy"
    echo "  retired previous name: $legacy"
  fi
done

# Register in the marketplace index, preserving any other experts already there.
node - "$MARKETPLACE" "$NAME" "$ROOT" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const [marketplace, name, root] = process.argv.slice(2);

const manifestPath = path.join(marketplace, ".codebuddy-plugin/marketplace.json");
const manifest = fs.existsSync(manifestPath)
  ? JSON.parse(fs.readFileSync(manifestPath, "utf8"))
  : { name: "my-experts", description: "my-experts marketplace (auto-generated)", plugins: [] };
manifest.plugins ??= [];

const plugin = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
const entry = {
  name,
  source: `./plugins/${name}`,
  description: plugin.displayDescription?.zh || plugin.description,
};
const LEGACY_NAMES = ["research-report-team", "report-loop"].filter((value) => value !== name);
manifest.plugins = manifest.plugins.filter((item) => !LEGACY_NAMES.includes(item.name));
const index = manifest.plugins.findIndex((item) => item.name === name);
if (index >= 0) manifest.plugins[index] = entry;
else manifest.plugins.push(entry);

fs.mkdirSync(path.dirname(manifestPath), { recursive: true });
fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`  marketplace index: ${manifest.plugins.length} expert(s)`);
NODE

# Add to the custom-expert visibility list(s) so the UI surfaces it.
node - "$WB_DIR" "$NAME" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const [wbDir, name] = process.argv.slice(2);

const customRoot = path.join(wbDir, "experts/custom");
let dirs = fs.existsSync(customRoot)
  ? fs.readdirSync(customRoot, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name)
  : [];
if (dirs.length === 0) {
  dirs = [crypto.randomUUID()];
  fs.mkdirSync(path.join(customRoot, dirs[0]), { recursive: true });
}
for (const dir of dirs) {
  const file = path.join(customRoot, dir, "experts.json");
  const LEGACY_NAMES = ["research-report-team", "report-loop"].filter((value) => value !== name);
  let list = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : [];
  list = list.filter((value) => !LEGACY_NAMES.includes(value));
  if (!list.includes(name)) list.push(name);
  fs.writeFileSync(file, `${JSON.stringify(list, null, 2)}\n`);
  console.log(`  registered in experts/custom/${dir}`);
}
NODE

echo
echo "Installed: $TARGET"
echo "  expert:  $NAME ($VERSION, Team type)"
echo "  lead:    $(node -p "require('$ROOT/settings.json').agent")"
echo
echo "Restart WorkBuddy, then look for the expert in 专家 → 我的专家."
