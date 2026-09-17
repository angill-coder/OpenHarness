import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
const pluginName = manifest.name;
const version = manifest.version;
// Judge model defaults are owned by report_loop/core/judge_model.py so the
// Python runner and this builder can never drift apart. Parse them rather than
// restating the values here.
function readJudgeDefaults() {
  const source = fs.readFileSync(
    path.join(root, "report_loop/core/judge_model.py"),
    "utf8",
  );
  const read = (name) => {
    const match = source.match(new RegExp(`^${name}\\s*=\\s*"([^"]+)"`, "mu"));
    if (!match) throw new Error(`judge_model.py is missing ${name}`);
    return match[1];
  };
  return {
    workbuddy: { model: read("JUDGE_MODEL"), effort: read("JUDGE_EFFORT") },
    codex: { model: read("CODEX_JUDGE_MODEL"), effort: read("CODEX_JUDGE_EFFORT") },
  };
}
const judgeDefaults = readJudgeDefaults();
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
const platformKey = `${targetPlatform}-${targetArch}`;
const releaseRoot = path.join(root, "release");
const promptSuffix = "";
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

buildSync({
  entryPoints: [path.join(root, "hooks/capture-checkpoint.mjs")],
  outfile: path.join(pluginDir, "dist/capture-checkpoint.mjs"),
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  legalComments: "none",
});
for (const item of [
  ".codebuddy-plugin/plugin.json",
  "settings.json",
  "agents",
  "avatars",
  "skills",
  "resources/evidence",
  "report_loop",
  "rubrics",
  "docs",
  "hooks/hooks.json",
  "scripts/run-node.sh",
  "scripts/run-node.cmd",
  "scripts/run-python.sh",
  "scripts/run-python.cmd",
  "README.md",
  "LICENSE.md",
]) copyPlugin(item);

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

const pluginRootToken = "${CODEBUDDY_PLUGIN_ROOT}";
const releaseHooksPath = path.join(pluginDir, "hooks/hooks.json");
const releaseHooks = JSON.parse(fs.readFileSync(releaseHooksPath, "utf8"));
for (const registrations of Object.values(releaseHooks.hooks)) {
  for (const registration of registrations) {
    for (const hook of registration.hooks ?? []) {
      const mode = hook.command.trim().split(/\s+/u).at(-1);
      hook.command = targetPlatform === "win32"
        ? `cmd.exe /d /c call "${pluginRootToken}\\scripts\\run-node.cmd" `
          + `"${pluginRootToken}\\dist\\capture-checkpoint.mjs" ${mode}`
        : "RESEARCH_REPORT_CAPTURE_HOOK_DIR=$HOME/.research-report-loop/capture-hook-state "
          + `sh "${pluginRootToken}/scripts/run-node.sh" `
          + `"${pluginRootToken}/dist/capture-checkpoint.mjs" ${mode}`;
    }
  }
}
fs.writeFileSync(releaseHooksPath, `${JSON.stringify(releaseHooks, null, 2)}\n`);

const releaseManifestPath = path.join(pluginDir, ".codebuddy-plugin/plugin.json");
const releaseManifest = JSON.parse(fs.readFileSync(releaseManifestPath, "utf8"));
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
  judgeProviders: ["workbuddy", "codex"],
  defaultJudgeProvider: "workbuddy",
  judgeDefaults,
  judgeFallbackProvider: "workbuddy",
  judgeFallbackModelSource: "workbuddy-trace-requestModelId",
  judgeFallbackTriggers: ["transport_error", "empty_response", "invalid_judge_json"],
  intakeUserEvidenceRequired: true,
  judgePromptTransport: "stdin",
  reportLoopLaunchBoundary: "workbuddy-host-hook",
  reportLoopLaunchTransport: "hook-spawned-worker",
  reportLoopTrigger: "report-loop-job-write",
  reportLoopWaitTransport: "workbuddy-background-task",
  reportLoopWaitNotification: "task-notification-then-TaskOutput",
  reportLoopResultTransport: "job-sibling-json",
}, null, 2)}\n`);

const marketplace = {
  name: "report-agent-v3-local",
  description: "Installable local marketplace for the report-agent-v3 research report team",
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
  `Release built: ${outputDir}`
  + `\nDefault Judge: workbuddy / ${judgeDefaults.workbuddy.model}`
  + ` / ${judgeDefaults.workbuddy.effort}; optional: codex / ${judgeDefaults.codex.model}`
  + ` / ${judgeDefaults.codex.effort}\n`,
);
