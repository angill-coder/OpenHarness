import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceManifest = JSON.parse(
  fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
);
const version = sourceManifest.version;
const pluginName = sourceManifest.name;
const expertPluginName = "report-loop";
const releaseRoot = path.join(root, "release");
const stagingName = `${pluginName}-${version}-${process.platform}-${process.arch}`;
const stagingDir = path.join(releaseRoot, stagingName, "plugins", pluginName);
const expertName = `${expertPluginName}-expert-${version}`;
const expertDir = path.join(releaseRoot, expertName);
const zipPath = `${expertDir}.zip`;
const noArchive = process.argv.includes("--no-archive");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) throw new Error(`${command} failed with exit code ${result.status}`);
}

function writeJson(target, value) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`);
}

function prepareExpertSubagent(target) {
  const content = fs.readFileSync(target, "utf8");
  const updated = content
    .replace(/^tools:.*\n/mu, "")
    .replace(
      /^(# Research Report Memory (?:Curator|Reflection))$/mu,
      "$1\n\n在本 Expert 中只调用 `mcp__report-expert-v2__*` 工具；即使同时发现原插件的 `report-memory-v2`，也不使用后者。",
    );
  fs.writeFileSync(target, updated);
}

function writeExpertReflectionLaunchers() {
  fs.writeFileSync(path.join(expertDir, "scripts/reflection-current.sh"), `#!/bin/sh
set -eu

WORKBUDDY_DIR=\${CODEBUDDY_CONFIG_DIR:-\${WORKBUDDY_CONFIG_DIR:-$HOME/.workbuddy}}
PLUGIN_ROOT="$WORKBUDDY_DIR/plugins/marketplaces/my-experts/plugins/${expertPluginName}"
RUNNER="$PLUGIN_ROOT/scripts/run-memory-reflection-workbuddy.sh"
if [ ! -f "$RUNNER" ]; then
  echo "Report Expert Reflection runner not found: $RUNNER" >&2
  exit 1
fi

RESEARCH_REPORT_REFLECTION_MCP_NAME=report-expert-v2 \\
RESEARCH_REPORT_REFLECTION_NODE_RUNNER="$PLUGIN_ROOT/bin/run-node" \\
exec /bin/sh "$RUNNER"
`);
  fs.chmodSync(path.join(expertDir, "scripts/reflection-current.sh"), 0o755);

  fs.writeFileSync(path.join(expertDir, "scripts/reflection-current.ps1"), `$ErrorActionPreference = "Stop"
$WorkBuddyConfig = if ($env:CODEBUDDY_CONFIG_DIR) {
    $env:CODEBUDDY_CONFIG_DIR
} else {
    Join-Path $HOME ".workbuddy"
}
$PluginRoot = Join-Path $WorkBuddyConfig "plugins\\marketplaces\\my-experts\\plugins\\${expertPluginName}"
$Runner = Join-Path $PluginRoot "scripts\\run-memory-reflection-workbuddy.ps1"
if (-not (Test-Path $Runner)) { throw "Report Expert Reflection runner not found: $Runner" }
$env:RESEARCH_REPORT_REFLECTION_MCP_NAME = "report-expert-v2"
$env:RESEARCH_REPORT_REFLECTION_NODE_RUNNER = Join-Path $PluginRoot "bin\\run-node.cmd"
& $Runner
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
`);
}

run(process.execPath, ["scripts/build-release.mjs", "--no-archive"]);
if (!fs.statSync(stagingDir, { throwIfNoEntry: false })?.isDirectory()) {
  throw new Error(`Expert staging release not found: ${stagingDir}`);
}

fs.rmSync(expertDir, { recursive: true, force: true });
fs.rmSync(zipPath, { force: true });
fs.rmSync(`${zipPath}.sha256`, { force: true });
fs.cpSync(stagingDir, expertDir, { recursive: true });

fs.rmSync(path.join(expertDir, ".mcp.json"), { force: true });
fs.rmSync(path.join(expertDir, "docs"), { recursive: true, force: true });
fs.rmSync(path.join(expertDir, "hooks"), { recursive: true, force: true });
fs.rmSync(path.join(expertDir, "dist/capture-checkpoint.mjs"), { force: true });
for (const unused of [
  "register-workbuddy-local.mjs",
  "migrate-rubric-scope-paths.mjs",
  "verify-mcp-contract.mjs",
  "run-node.sh",
  "run-node.cmd",
]) {
  fs.rmSync(path.join(expertDir, "scripts", unused), { force: true });
}

fs.mkdirSync(path.join(expertDir, "bin"), { recursive: true });
fs.copyFileSync(path.join(root, "scripts/run-node.sh"), path.join(expertDir, "bin/run-node"));
fs.copyFileSync(path.join(root, "scripts/run-node.cmd"), path.join(expertDir, "bin/run-node.cmd"));
fs.copyFileSync(
  path.join(stagingDir, "dist/capture-checkpoint.mjs"),
  path.join(expertDir, "bin/capture-checkpoint.mjs"),
);
fs.chmodSync(path.join(expertDir, "bin/run-node"), 0o755);

fs.copyFileSync(
  path.join(root, "expert/report-expert-v1.md"),
  path.join(expertDir, "agents/report-expert-v1.md"),
);
for (const agent of [
  "research-report-memory-curator.md",
  "research-report-memory-reflection.md",
]) {
  prepareExpertSubagent(path.join(expertDir, "agents", agent));
}
fs.mkdirSync(path.join(expertDir, "avatars"), { recursive: true });
fs.copyFileSync(path.join(root, "expert/avatars/expert.png"), path.join(expertDir, "avatars/expert.png"));
fs.copyFileSync(path.join(root, "expert/README.md"), path.join(expertDir, "README.md"));
writeExpertReflectionLaunchers();

const hookCommand = '"${CODEBUDDY_PLUGIN_ROOT}/bin/run-node" '
  + '"${CODEBUDDY_PLUGIN_ROOT}/bin/capture-checkpoint.mjs"';
const hooks = {
  description: "Report Loop launcher and writing-feedback capture reminder.",
  hooks: Object.fromEntries(["UserPromptSubmit", "PostToolUse", "Stop"].map((event) => [event, [{
    matcher: "*",
    hooks: [{
      type: "command",
      command: `${hookCommand} ${event === "UserPromptSubmit" ? "prompt" : event === "PostToolUse" ? "post-tool" : "stop"}`,
      timeout: 5,
    }],
  }]])),
};
writeJson(path.join(expertDir, "hooks.json"), hooks);

const initPrompt = {
  zh: "请根据我提供的材料，写一份经过自动评测和改写的研究报告。",
  en: "Create a research report from my materials and improve it through the automated report loop.",
};
const expertManifest = {
  ...sourceManifest,
  name: expertPluginName,
  agents: [
    "./agents/report-expert-v1.md",
    "./agents/research-report-memory-curator.md",
    "./agents/research-report-memory-reflection.md",
  ],
  skills: ["./skills/research-report-loop"],
  hooks: "./hooks.json",
  mcpServers: {
    "report-expert-v2": {
      command: "${CODEBUDDY_PLUGIN_ROOT}/bin/run-node",
      args: ["${CODEBUDDY_PLUGIN_ROOT}/dist/memory-server.mjs"],
      env: {
        RESEARCH_REPORT_MEMORY_V2_0821_DIR: "~/.research-report-memory-v2-0821",
        RESEARCH_REPORT_BASE_RUBRIC_PATH: "${CODEBUDDY_PLUGIN_ROOT}/rubrics/v2_rubric_research.json",
      },
    },
  },
  expertType: "agent",
  agentName: "report-expert-v1",
  displayName: { en: "report-expert-V1", zh: "报告专家V1" },
  profession: { en: "Research Report Consultant", zh: "研究报告顾问" },
  displayDescription: {
    en: "Turns interviews, surveys, data, and documents into evaluated research reports, then learns durable writing preferences from user feedback.",
    zh: "将访谈、问卷、数据和文档转化为经过自动评测与迭代改写的研究报告，并持续学习用户的长期写作要求。",
  },
  avatar: "avatars/expert.png",
  categoryId: "12-IndustryConsultant",
  defaultInitPrompt: initPrompt,
  plugin: expertPluginName,
  tags: [
    { en: "Research Report", zh: "研究报告" },
    { en: "Report Review", zh: "自动评测" },
    { en: "Writing Memory", zh: "写作记忆" },
  ],
  quickPrompts: [
    initPrompt,
    {
      en: "Turn these interviews and survey results into a management research report.",
      zh: "把这些访谈和问卷结果整理成一份面向管理层的研究报告。",
    },
    {
      en: "Revise this report based on my feedback and remember reusable writing preferences.",
      zh: "根据我的反馈修改这份报告，并沉淀可复用的写作要求。",
    },
  ],
};
writeJson(path.join(expertDir, ".codebuddy-plugin/plugin.json"), expertManifest);

const buildInfoPath = path.join(expertDir, "BUILD-INFO.json");
const buildInfo = JSON.parse(fs.readFileSync(buildInfoPath, "utf8"));
delete buildInfo.platform;
delete buildInfo.arch;
writeJson(buildInfoPath, {
  ...buildInfo,
  name: expertPluginName,
  packageType: "workbuddy-expert",
  expertType: "agent",
  supportedPlatforms: ["darwin", "win32"],
  crossPlatformLauncher: "bin/run-node + bin/run-node.cmd",
});

const forbidden = ["hooks", "commands"].filter((name) => fs.existsSync(path.join(expertDir, name)));
if (forbidden.length > 0) throw new Error(`Forbidden Expert directories: ${forbidden.join(", ")}`);
for (const agent of expertManifest.agents) {
  const content = fs.readFileSync(path.join(expertDir, agent.replace(/^\.\//u, "")), "utf8");
  const frontmatter = content.match(/^---\n([\s\S]*?)\n---/u)?.[1] ?? "";
  if (/^tools:/mu.test(frontmatter)) throw new Error(`Expert Agent declares tools: ${agent}`);
}

if (!noArchive) {
  if (process.platform === "win32") {
    run("powershell.exe", [
      "-NoProfile",
      "-Command",
      `Compress-Archive -LiteralPath '${expertDir.replaceAll("'", "''")}' -DestinationPath '${zipPath.replaceAll("'", "''")}' -Force`,
    ]);
  } else {
    run("zip", ["-qry", path.basename(zipPath), expertName], { cwd: releaseRoot });
  }
  const digest = crypto.createHash("sha256").update(fs.readFileSync(zipPath)).digest("hex");
  fs.writeFileSync(`${zipPath}.sha256`, `${digest}  ${path.basename(zipPath)}\n`);
}

process.stdout.write(`WorkBuddy Expert built: ${expertDir}\n`);
