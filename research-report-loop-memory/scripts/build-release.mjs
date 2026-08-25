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
const requestedJudgeProvider = judgeProviderArgIndex >= 0
  ? process.argv[judgeProviderArgIndex + 1]
  : "workbuddy";
if (requestedJudgeProvider !== "workbuddy") {
  throw new Error("Only the WorkBuddy Judge provider is supported");
}
const judgeProvider = "workbuddy";
const judgeDefaults = {
  workbuddy: { model: "deepseek-v4-pro", effort: "medium" },
};
const noArchive = process.argv.includes("--no-archive");
const targetPlatformArgIndex = process.argv.indexOf("--target-platform");
const targetPlatform = targetPlatformArgIndex >= 0
  ? process.argv[targetPlatformArgIndex + 1]
  : process.platform;
const targetArchArgIndex = process.argv.indexOf("--target-arch");
const targetArch = targetArchArgIndex >= 0
  ? process.argv[targetArchArgIndex + 1]
  : process.arch;
if (!new Set(["darwin", "linux", "win32"]).has(targetPlatform)) {
  throw new Error(`Unsupported target platform: ${targetPlatform}`);
}
if (!targetArch?.trim()) throw new Error("Target architecture is required");
const curatorPromptSources = {
  "v1-gate-first": "agents/research-report-memory-curator.md",
  "v2-letta-first": "prompts/research-report-memory-curator-v2-letta-first.md",
};
if (!curatorPromptSources[curatorPromptVariant]) {
  throw new Error(`Unsupported curator prompt variant: ${curatorPromptVariant}`);
}
const platformKey = `${targetPlatform}-${targetArch}`;
const releaseRoot = path.join(root, "release");
const promptSuffix = curatorPromptExplicit ? `-prompt-${curatorPromptVariant}` : "";
const judgeSuffix = "";
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

const esbuildCli = path.join(root, "node_modules/esbuild/bin/esbuild");
run(process.execPath, [esbuildCli,
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
run(process.execPath, [esbuildCli,
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
  "scripts/run-node.cmd",
  "scripts/run-python.sh",
  "scripts/run-python.cmd",
  "scripts/register-workbuddy-local.mjs",
  "scripts/migrate-rubric-scope-paths.mjs",
  "scripts/verify-mcp-contract.mjs",
  "scripts/run-memory-reflection-workbuddy.sh",
  "scripts/run-memory-reflection-workbuddy.ps1",
  "scripts/reflection-current.sh",
  "scripts/install-reflection-macos.sh",
  "scripts/install-reflection-windows.ps1",
  "scripts/macos-reflection-schedule.plist.template",
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

const pluginRootToken = "${CODEBUDDY_PLUGIN_ROOT}";
const windowsCommand = (runner, target) =>
  `""${pluginRootToken}\\scripts\\${runner}" "${pluginRootToken}\\${target}""`;
const releaseMcp = {
  mcpServers: {
    "report-memory-v2": targetPlatform === "win32" ? {
      command: "cmd.exe",
      args: ["/d", "/s", "/c", windowsCommand("run-node.cmd", "dist\\memory-server.mjs")],
      env: {
        RESEARCH_REPORT_MEMORY_V2_0821_DIR: "~/.research-report-memory-v2-0821",
        RESEARCH_REPORT_BASE_RUBRIC_PATH: `${pluginRootToken}\\rubrics\\v2_rubric_research.json`,
      },
    } : {
      command: "sh",
      args: ["${CODEBUDDY_PLUGIN_ROOT}/scripts/run-node.sh", "${CODEBUDDY_PLUGIN_ROOT}/dist/memory-server.mjs"],
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
      const mode = hook.command.trim().split(/\s+/u).at(-1);
      hook.command = targetPlatform === "win32"
        ? `cmd.exe /d /s /c ""${pluginRootToken}\\scripts\\run-node.cmd" `
          + `"${pluginRootToken}\\dist\\capture-checkpoint.mjs" ${mode}"`
        : "RESEARCH_REPORT_CAPTURE_HOOK_DIR=$HOME/.research-report-memory-v2-0821/capture-hook-state "
          + `sh ${pluginRootToken}/scripts/run-node.sh `
          + `${pluginRootToken}/dist/capture-checkpoint.mjs ${mode}`;
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
  platform: targetPlatform,
  arch: targetArch,
  buildHostPlatform: process.platform,
  buildHostArch: process.arch,
  node: process.version,
  builtAt: new Date().toISOString(),
  curatorPromptVariant,
  judgeProviders: ["workbuddy"],
  defaultJudgeProvider: "workbuddy",
  judgeModel: judgeDefaults.workbuddy.model,
  judgeEffort: judgeDefaults.workbuddy.effort,
  judgeFallbackProvider: "workbuddy",
  judgeFallbackModelSource: "hostModel",
  judgeFallbackTriggers: ["transport_error", "empty_response", "invalid_judge_json"],
  intakeUserEvidenceRequired: true,
  judgePromptTransport: "stdin",
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
  "scripts/run-memory-reflection-workbuddy.sh",
  "scripts/reflection-current.sh",
  "scripts/install-reflection-macos.sh",
]) {
  fs.chmodSync(path.join(pluginDir, executable), 0o755);
}

fs.mkdirSync(releaseRoot, { recursive: true });
if (!noArchive) {
if (process.platform === "win32") {
  run("powershell.exe", [
    "-NoProfile",
    "-Command",
    `Compress-Archive -LiteralPath '${outputDir.replaceAll("'", "''")}' -DestinationPath '${zipPath.replaceAll("'", "''")}' -Force`,
  ]);
} else if (targetPlatform === "win32" || process.platform === "darwin") {
  run("zip", ["-qry", zipPath, packageName], { cwd: releaseRoot });
} else {
  run("tar", ["-czf", zipPath.replace(/\.zip$/u, ".tar.gz"), "-C", releaseRoot, packageName]);
}
if (fs.existsSync(zipPath)) {
  const digest = crypto.createHash("sha256").update(fs.readFileSync(zipPath)).digest("hex");
  fs.writeFileSync(`${zipPath}.sha256`, `${digest}  ${path.basename(zipPath)}\n`);
}
}
process.stdout.write(
  `Release built: ${outputDir}\nCurator prompt: ${curatorPromptVariant}`
  + `\nJudge: ${judgeProvider} / ${judgeDefaults[judgeProvider].model}`
  + ` / ${judgeDefaults[judgeProvider].effort}\n`,
);
