import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
const version = manifest.version;
const pluginName = manifest.name;
const marketplaceName = "research-report-memory-v2-mvp-local";
const platformKey = `${process.platform}-${process.arch}`;
const platformPackages = {
  "darwin-arm64": [],
  "darwin-x64": [],
  "linux-arm64": [],
  "linux-x64": [],
  "win32-x64": [],
};

if (!platformPackages[platformKey]) {
  throw new Error(`Unsupported release platform: ${platformKey}`);
}

const releaseRoot = path.join(root, "release");
const packageName = `${pluginName}-${version}-${platformKey}`;
const outputDir = path.join(releaseRoot, packageName);
const pluginDir = path.join(outputDir, "plugins", pluginName);
const zipPath = `${outputDir}.zip`;
const metaPath = path.join(releaseRoot, `${packageName}.esbuild-meta.json`);

if (!outputDir.startsWith(`${releaseRoot}${path.sep}`)) {
  throw new Error("Unsafe release output path");
}
fs.rmSync(outputDir, { recursive: true, force: true });
fs.rmSync(zipPath, { force: true });
fs.rmSync(`${zipPath}.sha256`, { force: true });
fs.mkdirSync(path.join(pluginDir, "dist"), { recursive: true });

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, encoding: "utf8", stdio: "inherit", ...options });
  if (result.status !== 0) throw new Error(`${command} failed with exit code ${result.status}`);
}

function copyPlugin(relativeSource, relativeTarget = relativeSource) {
  const source = path.join(root, relativeSource);
  const target = path.join(pluginDir, relativeTarget);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, { recursive: true });
}

function copyRoot(relativeSource, relativeTarget = relativeSource) {
  const source = path.join(root, relativeSource);
  const target = path.join(outputDir, relativeTarget);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, { recursive: true });
}

const esbuild = path.join(root, "node_modules/.bin/esbuild");
run(esbuild, [
  "mcp/src/server.ts",
  "--bundle",
  "--platform=node",
  "--format=esm",
  "--target=node22",
  `--outfile=${path.join(pluginDir, "dist/server.mjs")}`,
  `--metafile=${metaPath}`,
  '--banner:js=import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);',
  `--alias:ai=${path.join(root, "scripts/build-stubs/ai.mjs")}`,
  `--alias:@ai-sdk/openai=${path.join(root, "scripts/build-stubs/openai.mjs")}`,
  `--alias:undici=${path.join(root, "scripts/build-stubs/undici.mjs")}`,
  `--alias:@node-rs/jieba=${path.join(root, "scripts/build-stubs/jieba.mjs")}`,
  `--alias:@node-rs/jieba/dict.js=${path.join(root, "scripts/build-stubs/jieba-dict.mjs")}`,
  "--external:sqlite-vec",
  "--external:node-llama-cpp",
  "--external:openclaw/*",
  "--legal-comments=linked",
]);
run(esbuild, [
  "hooks/memory-guard.mjs",
  "--bundle",
  "--platform=node",
  "--format=esm",
  "--target=node22",
  `--outfile=${path.join(pluginDir, "dist/memory-guard.mjs")}`,
  "--legal-comments=linked",
]);

for (const item of [
  ".codebuddy-plugin/plugin.json",
  "agents",
  "skills",
  "scripts/run-node.sh",
  "scripts/run-memory-maintenance-workbuddy.sh",
  "scripts/install-maintenance-macos.sh",
  "scripts/register-workbuddy-local.mjs",
  "scripts/maintenance-launchagent.plist.template",
]) copyPlugin(item);
copyPlugin("LICENSE", "LICENSE.md");
copyPlugin("README.release.md", "README.md");
copyRoot("scripts/install-release-workbuddy.sh");
copyRoot("README.release.md", "README.md");
copyRoot("skillhub/SKILL.md", "SKILL.md");

const releaseMarketplace = {
  name: marketplaceName,
  description: "Installable local marketplace for the research-report writing memory plugin",
  owner: { name: "Research Report Team" },
  plugins: [{
    name: pluginName,
    description: manifest.description,
    version,
    source: `./plugins/${pluginName}`,
    license: manifest.license,
  }],
};
fs.mkdirSync(path.join(outputDir, ".codebuddy-plugin"), { recursive: true });
fs.writeFileSync(
  path.join(outputDir, ".codebuddy-plugin/marketplace.json"),
  `${JSON.stringify(releaseMarketplace, null, 2)}\n`,
);

// esbuild carries ordinary JavaScript dependencies inside dist/server.mjs.
// This upload-safe release has no native runtime packages.
const baseRuntimePackages = [];
for (const packagePath of [...baseRuntimePackages, ...platformPackages[platformKey]]) {
  const source = path.join(root, "node_modules", packagePath);
  if (!fs.existsSync(source)) {
    throw new Error(`Required runtime package is not installed for ${platformKey}: ${packagePath}`);
  }
  const target = path.join(pluginDir, "node_modules", packagePath);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, { recursive: true });
}

const sqliteVecStubDir = path.join(pluginDir, "node_modules", "sqlite-vec");
fs.mkdirSync(sqliteVecStubDir, { recursive: true });
fs.copyFileSync(path.join(root, "scripts/build-stubs/sqlite-vec.cjs"), path.join(sqliteVecStubDir, "index.cjs"));
fs.writeFileSync(path.join(sqliteVecStubDir, "package.json"), `${JSON.stringify({
  name: "sqlite-vec",
  version: "0.0.0-upload-safe",
  private: true,
  main: "index.cjs",
}, null, 2)}\n`);

const mcpConfig = {
  mcpServers: {
    [pluginName]: {
      command: "sh",
      args: [
        "${CODEBUDDY_PLUGIN_ROOT}/scripts/run-node.sh",
        "${CODEBUDDY_PLUGIN_ROOT}/dist/server.mjs",
      ],
    },
  },
};
fs.writeFileSync(path.join(pluginDir, ".mcp.json"), `${JSON.stringify(mcpConfig, null, 2)}\n`);

// Development uses an inline WorkBuddy MCP config that points at TypeScript.
// The release manifest must point at the generated bundled runtime instead.
const releasePluginManifestPath = path.join(pluginDir, ".codebuddy-plugin/plugin.json");
const releasePluginManifest = JSON.parse(fs.readFileSync(releasePluginManifestPath, "utf8"));
releasePluginManifest.name = pluginName;
releasePluginManifest.mcpServers = "./.mcp.json";
fs.writeFileSync(releasePluginManifestPath, `${JSON.stringify(releasePluginManifest, null, 2)}\n`);

const hookConfig = JSON.parse(fs.readFileSync(path.join(root, "hooks/hooks.json"), "utf8"));
for (const definitions of Object.values(hookConfig.hooks)) {
  for (const definition of definitions) {
    for (const hook of definition.hooks) {
      hook.command = hook.command.replace(
        " --import ${CODEBUDDY_PLUGIN_ROOT}/node_modules/tsx/dist/loader.mjs ${CODEBUDDY_PLUGIN_ROOT}/hooks/memory-guard.mjs",
        " ${CODEBUDDY_PLUGIN_ROOT}/dist/memory-guard.mjs",
      );
    }
  }
}
fs.mkdirSync(path.join(pluginDir, "hooks"), { recursive: true });
fs.writeFileSync(path.join(pluginDir, "hooks/hooks.json"), `${JSON.stringify(hookConfig, null, 2)}\n`);

fs.writeFileSync(path.join(pluginDir, "package.json"), `${JSON.stringify({
  name: `${pluginName}-runtime`,
  version,
  private: true,
  type: "module",
  engines: { node: ">=22.16.0" },
  os: [process.platform],
  cpu: [process.arch],
}, null, 2)}\n`);

fs.chmodSync(path.join(pluginDir, "scripts/run-node.sh"), 0o755);
fs.chmodSync(path.join(pluginDir, "scripts/run-memory-maintenance-workbuddy.sh"), 0o755);
fs.chmodSync(path.join(pluginDir, "scripts/install-maintenance-macos.sh"), 0o755);
fs.chmodSync(path.join(outputDir, "scripts/install-release-workbuddy.sh"), 0o755);

const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
const packageRoots = new Set([...baseRuntimePackages, ...platformPackages[platformKey]]);
for (const input of Object.keys(meta.inputs)) {
  const match = input.match(/node_modules\/(?:@[^/]+\/[^/]+|[^/]+)/u);
  if (match) packageRoots.add(match[0].slice("node_modules/".length));
}
const notices = [];
for (const packagePath of [...packageRoots].sort()) {
  const packageJson = path.join(root, "node_modules", packagePath, "package.json");
  if (!fs.existsSync(packageJson)) continue;
  const info = JSON.parse(fs.readFileSync(packageJson, "utf8"));
  notices.push(`- ${info.name}@${info.version} — ${typeof info.license === "string" ? info.license : "license in package metadata"}`);
}
fs.writeFileSync(
  path.join(pluginDir, "THIRD_PARTY_NOTICES.md"),
  `# Third-party notices\n\nThis distribution bundles or carries the following runtime dependencies:\n\n${notices.join("\n")}\n`,
);

const buildInfo = {
  name: pluginName,
  version,
  platform: process.platform,
  arch: process.arch,
  node: process.version,
  builtAt: new Date().toISOString(),
  sourceRuntime: "TencentDB Agent Memory 0.3.6",
};
fs.writeFileSync(path.join(pluginDir, "BUILD-INFO.json"), `${JSON.stringify(buildInfo, null, 2)}\n`);

// Finder metadata is not part of the product and wastes SkillHub's file quota.
function removeFinderMetadata(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) removeFinderMetadata(entryPath);
    else if (entry.name === ".DS_Store") fs.rmSync(entryPath, { force: true });
  }
}
removeFinderMetadata(outputDir);

if (process.platform === "darwin") {
  run("zip", ["-qry", zipPath, packageName], { cwd: releaseRoot });
} else {
  run("tar", ["-czf", zipPath.replace(/\.zip$/u, ".tar.gz"), "-C", releaseRoot, packageName]);
}

if (fs.existsSync(zipPath)) {
  const digest = crypto.createHash("sha256").update(fs.readFileSync(zipPath)).digest("hex");
  fs.writeFileSync(`${zipPath}.sha256`, `${digest}  ${path.basename(zipPath)}\n`);
}

const bytes = fs.statSync(path.join(pluginDir, "dist/server.mjs")).size;
process.stdout.write(`Release built: ${outputDir}\nBundled MCP: ${(bytes / 1024 / 1024).toFixed(2)} MB\n`);
