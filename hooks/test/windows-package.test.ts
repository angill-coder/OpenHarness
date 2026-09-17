import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("Windows and macOS launchers discover WorkBuddy runtimes", () => {
  const nodeCmd = fs.readFileSync(path.join(root, "scripts/run-node.cmd"), "utf8");
  const pythonCmd = fs.readFileSync(path.join(root, "scripts/run-python.cmd"), "utf8");
  const nodeSh = fs.readFileSync(path.join(root, "scripts/run-node.sh"), "utf8");
  const pythonSh = fs.readFileSync(path.join(root, "scripts/run-python.sh"), "utf8");

  const pythonCommandRunner = fs.readFileSync(path.join(root, "scripts/run-python-command.mjs"), "utf8");
  assert.match(nodeCmd, /WORKBUDDY_NODE/u);
  assert.match(nodeCmd, /CODEBUDDY_CODE_NODE_PATH/u);
  assert.match(nodeCmd, /CODEBUDDY_NODE_BIN/u);
  assert.match(nodeCmd, /WORKBUDDY_EXTRA_PATHS/u);
  assert.match(nodeCmd, /WORKBUDDY_CONFIG_DIR/u);
  assert.match(nodeCmd, /CODEBUDDY_CONFIG_DIR/u);
  assert.match(nodeCmd, /node\.exe/u);
  const nodeVersionCheck = nodeCmd
    .split(/\r?\n/u)
    .find((line) => line.includes("process.versions.node")) ?? "";
  assert.match(nodeVersionCheck, /a>22\|\|\(a===22&&b>=16\)/u);
  assert.doesNotMatch(nodeVersionCheck, /\^/u);
  assert.match(pythonCmd, /WORKBUDDY_PYTHON/u);
  assert.match(pythonCmd, /CODEBUDDY_CODE_PYTHON_PATH/u);
  assert.match(pythonCmd, /WORKBUDDY_EXTRA_PATHS/u);
  assert.match(pythonCmd, /WORKBUDDY_CONFIG_DIR/u);
  assert.match(pythonCmd, /CODEBUDDY_CONFIG_DIR/u);
  assert.match(pythonCmd, /PYTHONUTF8=1/u);
  assert.match(pythonCmd, /PYTHONIOENCODING=utf-8/u);
  assert.match(nodeSh, /WORKBUDDY_NODE/u);
  assert.match(nodeSh, /WORKBUDDY_EXTRA_PATHS/u);
  assert.match(nodeSh, /WORKBUDDY_CONFIG_DIR/u);
  assert.match(pythonSh, /WORKBUDDY_PYTHON/u);
  assert.match(pythonSh, /WORKBUDDY_EXTRA_PATHS/u);
  assert.match(pythonSh, /WORKBUDDY_CONFIG_DIR/u);
  assert.match(pythonCommandRunner, /\["\/d", "\/c", runner,/u);
  assert.doesNotMatch(pythonCommandRunner, /shell:\s*process\.platform/u);
  assert.match(pythonCommandRunner, /"cmd\.exe"/u);
});

test("release builder emits platform-native Hook configurations", () => {
  const builder = fs.readFileSync(path.join(root, "scripts/build-release.mjs"), "utf8");
  assert.match(builder, /--target-platform/u);
  assert.match(builder, /targetPlatform === "win32"/u);
  assert.match(builder, /cmd\.exe \/d \/c call/u);
  assert.match(builder, /run-node\.cmd/u);
  assert.match(builder, /run-node\.sh/u);
  assert.match(builder, /capture-checkpoint\.mjs/u);
  // Judge model defaults must be read from judge_model.py, never restated here.
  // A literal model id in the builder could silently drift from the runner's.
  assert.match(builder, /judge_model\.py/u);
  assert.match(builder, /JUDGE_MODEL/u);
  assert.doesNotMatch(builder, /gpt-5\.6-sol/u);
  const judgeModelSource = fs.readFileSync(
    path.join(root, "report_loop/core/judge_model.py"),
    "utf8",
  );
  assert.match(judgeModelSource, /^JUDGE_MODEL\s*=\s*"[^"]+"/mu);
  assert.match(judgeModelSource, /^CODEX_JUDGE_MODEL\s*=\s*"[^"]+"/mu);
  assert.match(builder, /defaultJudgeProvider: "workbuddy"/u);
  assert.match(builder, /judgeDefaults/u);
  assert.doesNotMatch(builder, /--judge-provider/u);
  assert.match(builder, /report_loop/u);
  assert.doesNotMatch(builder, /cmd\.exe \/d \/s|"\/s"/u);

  const completed = spawnSync(
    process.execPath,
    [path.join(root, "scripts/build-release.mjs"), "--target-platform", "win32", "--target-arch", "x64-hook-test", "--no-archive"],
    { cwd: root, encoding: "utf8" },
  );
  assert.equal(completed.status, 0, completed.stderr);
  const version = JSON.parse(
    fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
  ).version;
  const pluginDir = path.join(
    root,
    "release",
    `report-agent-v3-${version}-win32-x64-hook-test`,
    "plugins/report-agent-v3",
  );
  // 不再产出 .mcp.json 或 MCP server bundle
  assert.equal(fs.existsSync(path.join(pluginDir, ".mcp.json")), false);
  assert.equal(fs.existsSync(path.join(pluginDir, "dist/memory-server.mjs")), false);
  assert.equal(fs.existsSync(path.join(pluginDir, "dist/capture-checkpoint.mjs")), true);
  const hooks = JSON.parse(fs.readFileSync(path.join(pluginDir, "hooks/hooks.json"), "utf8"));
  for (const registrations of Object.values(hooks.hooks) as Array<Array<{ hooks: Array<{ command: string }> }>>) {
    const command = registrations[0].hooks[0].command;
    assert.match(command, /^cmd\.exe \/d \/c call "\$\{CODEBUDDY_PLUGIN_ROOT\}\\scripts\\run-node\.cmd" /u);
    assert.match(command, /"\$\{CODEBUDDY_PLUGIN_ROOT\}\\dist\\capture-checkpoint\.mjs" (?:prompt|post-tool|stop)$/u);
    assert.doesNotMatch(command, /\/s|""|\bsh\b|run-node\.sh/u);
  }
});

test("Report Loop uses one host launcher and keeps Python as the only loop runtime", () => {
  assert.equal(fs.existsSync(path.join(root, "report_loop/runner.py")), true);
  assert.equal(fs.existsSync(path.join(root, "hooks/lib/report-loop-launcher.ts")), true);
  assert.equal(fs.existsSync(path.join(root, "report_loop/server.py")), false);
  assert.equal(fs.existsSync(path.join(root, "report_loop/core/codex_cli.py")), true);

  // Hook 直接 spawn Python Runner，无 MCP 中间层
  assert.equal(fs.existsSync(path.join(root, ".mcp.json")), false);
  assert.equal(fs.existsSync(path.join(root, "mcp")), false);
  assert.equal(fs.existsSync(path.join(root, "hooks/lib/report-loop-launcher.ts")), true);
});

