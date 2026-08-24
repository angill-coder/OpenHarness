import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
const pluginName = manifest.name;
const version = manifest.version;
const platformKey = `${process.platform}-${process.arch}`;
const releaseRoot = path.join(root, "release");
const packageName = `${pluginName}-${version}-${platformKey}`;
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
  "hooks/memory-guard.mjs",
  "--bundle",
  "--platform=node",
  "--format=esm",
  "--target=node22",
  `--outfile=${path.join(pluginDir, "dist/memory-guard.mjs")}`,
  "--legal-comments=none",
]);

for (const item of [
  ".codebuddy-plugin/plugin.json",
  "agents",
  "skills",
  "mcp/report_loop",
  "rubrics",
  "docs",
  "hooks/report-loop-guard.py",
  "scripts/run-node.sh",
  "scripts/run-python.sh",
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
        RESEARCH_REPORT_LOOP_JUDGE_MODEL: "deepseek-v4-flash-ioa",
        RESEARCH_REPORT_LOOP_JUDGE_EFFORT: "medium",
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

const releaseManifestPath = path.join(pluginDir, ".codebuddy-plugin/plugin.json");
const releaseManifest = JSON.parse(fs.readFileSync(releaseManifestPath, "utf8"));
releaseManifest.mcpServers = "./.mcp.json";
fs.writeFileSync(releaseManifestPath, `${JSON.stringify(releaseManifest, null, 2)}\n`);

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
  engines: { node: ">=22.16.0", python: ">=3.10" },
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

for (const executable of ["scripts/run-node.sh", "scripts/run-python.sh"]) {
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
process.stdout.write(`Release built: ${outputDir}\n`);
