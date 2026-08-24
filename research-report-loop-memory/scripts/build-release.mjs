import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
const pluginName = manifest.name;
const version = manifest.version;
const curatorPromptArgIndex = process.argv.indexOf("--curator-prompt");
const curatorPromptExplicit = curatorPromptArgIndex >= 0;
const curatorPromptVariant = curatorPromptExplicit
  ? process.argv[curatorPromptArgIndex + 1]
  : "v1-gate-first";
const judgeProviderArgIndex = process.argv.indexOf("--judge-provider");
const judgeProviderExplicit = judgeProviderArgIndex >= 0;
const judgeProvider = judgeProviderExplicit
  ? process.argv[judgeProviderArgIndex + 1]
  : "codex";
const judgeDefaults = {
  codex: { model: "gpt-5.6-sol", effort: "medium" },
  workbuddy: { model: "deepseek-v4-flash-ioa", effort: "medium" },
};
if (!judgeDefaults[judgeProvider]) {
  throw new Error(`Unsupported judge provider: ${judgeProvider}`);
}
const curatorPromptSources = {
  "v1-gate-first": "agents/research-report-memory-curator.md",
  "v2-letta-first": "prompts/research-report-memory-curator-v2-letta-first.md",
};
if (!curatorPromptSources[curatorPromptVariant]) {
  throw new Error(`Unsupported curator prompt variant: ${curatorPromptVariant}`);
}
const platformKey = `${process.platform}-${process.arch}`;
const releaseRoot = path.join(root, "release");
const promptSuffix = curatorPromptExplicit ? `-prompt-${curatorPromptVariant}` : "";
const judgeSuffix = judgeProviderExplicit ? `-judge-${judgeProvider}` : "";
const packageName = `${pluginName}-${version}${promptSuffix}${judgeSuffix}-${platformKey}`;
const outputDir = path.join(releaseRoot, packageName);
const pluginDir = path.join(outputDir, "plugins", pluginName);
const zipPath = `${outputDir}.zip`;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) throw new Error(`${command} failed with exit code ${result.status}`);
}

function copyPlugin(source, target = source) {
  const from = path.join(root, source);
  const to = path.join(pluginDir, target);
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.cpSync(from, to, { recursive: true });
}

fs.rmSync(outputDir, { recursive: true, force: true });
fs.rmSync(zipPath, { force: true });
fs.rmSync(`${zipPath}.sha256`, { force: true });
fs.mkdirSync(path.join(pluginDir, "dist"), { recursive: true });

const esbuild = path.join(root, "node_modules/.bin/esbuild");
run(esbuild, [
  "mcp/src/server.ts",
  "--bundle",
  "--platform=node",
  "--format=esm",
  "--target=node22",
  `--outfile=${path.join(pluginDir, "dist/memory-server.mjs")}`,
  '--banner:js=import { createRequire as __createRequire } from "node:module"; const require = __createRequire(import.meta.url);',
  `--alias:ai=${path.join(root, "scripts/build-stubs/ai.mjs")}`,
  `--alias:@ai-sdk/openai=${path.join(root, "scripts/build-stubs/openai.mjs")}`,
  `--alias:undici=${path.join(root, "scripts/build-stubs/undici.mjs")}`,
  `--alias:@node-rs/jieba=${path.join(root, "scripts/build-stubs/jieba.mjs")}`,
  `--alias:@node-rs/jieba/dict.js=${path.join(root, "scripts/build-stubs/jieba-dict.mjs")}`,
  "--external:sqlite-vec",
  "--external:node-llama-cpp",
  "--external:openclaw/*",
  "--legal-comments=none",
]);
run(esbuild, [
  "hooks/capture-checkpoint.mjs",
  "--bundle",
  "--platform=node",
  "--format=esm",
  "--target=node22",
  `--outfile=${path.join(pluginDir, "dist/capture-checkpoint.mjs")}`,
  "--legal-comments=none",
]);
for (const item of [
  ".codebuddy-plugin/plugin.json",
  "agents",
  "skills",
  "mcp/report_loop",
  "rubrics",
  "docs",
  "hooks/hooks.json",
  "scripts/run-node.sh",
  "scripts/run-python.sh",
  "scripts/register-workbuddy-local.mjs",
  "scripts/migrate-rubric-scope-paths.mjs",
  "scripts/run-memory-maintenance-workbuddy.sh",
  "scripts/install-maintenance-macos.sh",
  "scripts/maintenance-launchagent.plist.template",
  "README.md",
  "LICENSE.md",
]) copyPlugin(item);
fs.copyFileSync(
  path.join(root, curatorPromptSources[curatorPromptVariant]),
  path.join(pluginDir, "agents/research-report-memory-curator.md"),
);

function removeGeneratedFiles(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory() && entry.name === "__pycache__") {
      fs.rmSync(entryPath, { recursive: true, force: true });
    } else if (entry.isDirectory()) {
      removeGeneratedFiles(entryPath);
    } else if (entry.name === ".DS_Store" || entry.name.endsWith(".pyc")) {
      fs.rmSync(entryPath, { force: true });
    }
  }
}
removeGeneratedFiles(pluginDir);

const sqliteVecDir = path.join(pluginDir, "node_modules/sqlite-vec");
fs.mkdirSync(sqliteVecDir, { recursive: true });
fs.copyFileSync(path.join(root, "scripts/build-stubs/sqlite-vec.cjs"), path.join(sqliteVecDir, "index.cjs"));
fs.writeFileSync(path.join(sqliteVecDir, "package.json"), `${JSON.stringify({
  name: "sqlite-vec",
  version: "0.0.0-upload-safe",
  private: true,
  main: "index.cjs",
}, null, 2)}\n`);

const releaseMcp = {
  mcpServers: {
    "research-report-loop": {
      command: "sh",
      args: [
        "${CODEBUDDY_PLUGIN_ROOT}/scripts/run-python.sh",
        "${CODEBUDDY_PLUGIN_ROOT}/mcp/report_loop/server.py",
      ],
      env: {
        RESEARCH_REPORT_LOOP_DIR: "~/.research-report-loop",
        RESEARCH_REPORT_LOOP_JUDGE_PROVIDER: judgeProvider,
        [judgeProvider === "codex"
          ? "RESEARCH_REPORT_LOOP_CODEX_MODEL"
          : "RESEARCH_REPORT_LOOP_WB_MODEL"]: judgeDefaults[judgeProvider].model,
        RESEARCH_REPORT_LOOP_JUDGE_EFFORT: judgeDefaults[judgeProvider].effort,
        RESEARCH_REPORT_MEMORY_V2_0821_DIR: "~/.research-report-memory-v2-0821",
      },
    },
    "research-report-memory-v2-0821": {
      command: "sh",
      args: [
        "${CODEBUDDY_PLUGIN_ROOT}/scripts/run-node.sh",
        "${CODEBUDDY_PLUGIN_ROOT}/dist/memory-server.mjs",
      ],
      env: {
        RESEARCH_REPORT_MEMORY_V2_0821_DIR: "~/.research-report-memory-v2-0821",
        RESEARCH_REPORT_BASE_RUBRIC_PATH: "${CODEBUDDY_PLUGIN_ROOT}/rubrics/v2_rubric_research.json",
      },
    },
  },
};
fs.writeFileSync(path.join(pluginDir, ".mcp.json"), `${JSON.stringify(releaseMcp, null, 2)}\n`);

const releaseHooksPath = path.join(pluginDir, "hooks/hooks.json");
const releaseHooks = JSON.parse(fs.readFileSync(releaseHooksPath, "utf8"));
for (const registrations of Object.values(releaseHooks.hooks)) {
  for (const registration of registrations) {
    for (const hook of registration.hooks ?? []) {
      hook.command = "RESEARCH_REPORT_CAPTURE_HOOK_DIR=$HOME/.research-report-memory-v2-0821/capture-hook-state "
        + "sh ${CODEBUDDY_PLUGIN_ROOT}/scripts/run-node.sh "
        + "${CODEBUDDY_PLUGIN_ROOT}/dist/capture-checkpoint.mjs "
        + hook.command.trim().split(/\s+/u).at(-1);
    }
  }
}
fs.writeFileSync(releaseHooksPath, `${JSON.stringify(releaseHooks, null, 2)}\n`);

const releaseManifestPath = path.join(pluginDir, ".codebuddy-plugin/plugin.json");
const releaseManifest = JSON.parse(fs.readFileSync(releaseManifestPath, "utf8"));
releaseManifest.mcpServers = "./.mcp.json";
fs.writeFileSync(releaseManifestPath, `${JSON.stringify(releaseManifest, null, 2)}\n`);

fs.writeFileSync(path.join(pluginDir, "package.json"), `${JSON.stringify({
  name: `${pluginName}-runtime`,
  version,
  private: true,
  type: "module",
  engines: { node: ">=22.16.0", python: ">=3.10" },
}, null, 2)}\n`);

fs.writeFileSync(path.join(pluginDir, "BUILD-INFO.json"), `${JSON.stringify({
  name: pluginName,
  version,
  platform: process.platform,
  arch: process.arch,
  node: process.version,
  builtAt: new Date().toISOString(),
  curatorPromptVariant,
  judgeProvider,
  judgeModel: judgeDefaults[judgeProvider].model,
  judgeEffort: judgeDefaults[judgeProvider].effort,
}, null, 2)}\n`);

const marketplace = {
  name: "research-report-loop-memory-local",
  description: "Installable local marketplace for Research Report Loop and Memory",
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
  `${JSON.stringify(marketplace, null, 2)}\n`,
);
fs.copyFileSync(path.join(root, "README.md"), path.join(outputDir, "README.md"));

for (const executable of [
  "scripts/run-node.sh",
  "scripts/run-python.sh",
  "scripts/run-memory-maintenance-workbuddy.sh",
  "scripts/install-maintenance-macos.sh",
]) {
  fs.chmodSync(path.join(pluginDir, executable), 0o755);
}

fs.mkdirSync(releaseRoot, { recursive: true });
if (process.platform === "darwin") {
  run("zip", ["-qry", zipPath, packageName], { cwd: releaseRoot });
} else {
  run("tar", ["-czf", zipPath.replace(/\.zip$/u, ".tar.gz"), "-C", releaseRoot, packageName]);
}
if (fs.existsSync(zipPath)) {
  const digest = crypto.createHash("sha256").update(fs.readFileSync(zipPath)).digest("hex");
  fs.writeFileSync(`${zipPath}.sha256`, `${digest}  ${path.basename(zipPath)}\n`);
}
process.stdout.write(
  `Release built: ${outputDir}\nCurator prompt: ${curatorPromptVariant}`
  + `\nJudge: ${judgeProvider} / ${judgeDefaults[judgeProvider].model}`
  + ` / ${judgeDefaults[judgeProvider].effort}\n`,
);
