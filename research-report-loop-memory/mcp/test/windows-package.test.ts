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

  assert.match(nodeCmd, /WORKBUDDY_NODE/u);
  assert.match(nodeCmd, /\.workbuddy\\binaries\\node\\versions/u);
  assert.match(nodeCmd, /node\.exe/u);
  assert.match(pythonCmd, /WORKBUDDY_PYTHON/u);
  assert.match(pythonCmd, /PYTHONUTF8=1/u);
  assert.match(pythonCmd, /PYTHONIOENCODING=utf-8/u);
  assert.match(nodeSh, /WORKBUDDY_NODE/u);
  assert.match(nodeSh, /\.workbuddy\/binaries\/node\/versions/u);
  assert.match(pythonSh, /WORKBUDDY_PYTHON/u);
  assert.match(pythonSh, /\.workbuddy\/binaries\/python\/versions/u);
});

test("Reflection schedules resolve the currently installed plugin on both platforms", () => {
  const windowsInstaller = fs.readFileSync(path.join(root, "scripts/install-reflection-windows.ps1"), "utf8");
  const windowsLauncher = fs.readFileSync(path.join(root, "scripts/reflection-current.ps1"), "utf8");
  const windowsReflection = fs.readFileSync(path.join(root, "scripts/run-memory-reflection-workbuddy.ps1"), "utf8");
  const macInstaller = fs.readFileSync(path.join(root, "scripts/install-reflection-macos.sh"), "utf8");
  const macLauncher = fs.readFileSync(path.join(root, "scripts/reflection-current.sh"), "utf8");

  assert.match(windowsInstaller, /reflection-current\.ps1/u);
  assert.match(windowsInstaller, /RESEARCH_REPORT_MEMORY_V2_0821_DIR/u);
  assert.match(windowsLauncher, /installed_plugins\.json/u);
  assert.match(windowsLauncher, /research-report-loop-memory@/u);
  assert.match(windowsLauncher, /run-memory-reflection-workbuddy\.ps1/u);
  assert.match(windowsReflection, /Join-Path \$WorkBuddyConfig "binaries\\node\\versions"/u);
  assert.match(macInstaller, /reflection-current\.sh/u);
  assert.match(macInstaller, /DATA_DIR=/u);
  assert.match(macLauncher, /installed_plugins\.json/u);
});

test("release builder emits platform-native Memory MCP and Hook configurations", () => {
  const builder = fs.readFileSync(path.join(root, "scripts/build-release.mjs"), "utf8");
  assert.match(builder, /--target-platform/u);
  assert.match(builder, /targetPlatform === "win32"/u);
  assert.match(builder, /command: "cmd\.exe"/u);
  assert.match(builder, /run-node\.cmd/u);
  assert.match(builder, /command: "sh"/u);
  assert.match(builder, /run-node\.sh/u);
  assert.match(builder, /capture-checkpoint\.mjs/u);
  assert.match(builder, /deepseek-v4-pro-ioa/u);
  assert.match(builder, /reflection-current\.ps1/u);
  assert.match(builder, /defaultJudgeProvider: "workbuddy"/u);
  assert.match(builder, /judgeDefaults/u);
  assert.doesNotMatch(builder, /--judge-provider/u);
  assert.match(builder, /mcp\/report_loop/u);
});

test("Report Loop uses one host launcher and keeps Python as the only loop runtime", () => {
  assert.equal(fs.existsSync(path.join(root, "mcp/report_loop/runner.py")), true);
  assert.equal(fs.existsSync(path.join(root, "mcp/src/report-loop-launcher.ts")), true);
  assert.equal(fs.existsSync(path.join(root, "mcp/report_loop/server.py")), false);
  assert.equal(fs.existsSync(path.join(root, "mcp/report_loop/core/codex_cli.py")), true);

  const sourceMcp = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  assert.deepEqual(Object.keys(sourceMcp.mcpServers), ["report-memory-v2"]);

  const preflight = fs.readFileSync(path.join(root, "scripts/verify-mcp-contract.mjs"), "utf8");
  assert.match(preflight, /writing_memory_recall/u);
  assert.match(preflight, /writing_memory_capture_payload/u);
  assert.match(preflight, /report_loop_run/u);
  assert.doesNotMatch(preflight, /"writing_memory_capture"/u);
  assert.doesNotMatch(preflight, /report_loop_(?:start|submit|finish|status)/u);
});

test("local registration replaces legacy Report Loop MCP with the unified host launcher", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-register-"));
  const configDir = path.join(temporary, "config");
  const marketplaceRoot = path.join(temporary, "marketplace");
  const installPath = path.join(marketplaceRoot, "plugins/research-report-loop-memory");
  fs.mkdirSync(path.join(marketplaceRoot, ".codebuddy-plugin"), { recursive: true });
  fs.writeFileSync(
    path.join(marketplaceRoot, ".codebuddy-plugin/marketplace.json"),
    JSON.stringify({ name: "test-marketplace", plugins: [] }),
  );
  fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(
    path.join(configDir, "settings.json"),
    JSON.stringify({ sandbox: { extraAllowWrite: ["/already-allowed"] } }),
  );
  fs.writeFileSync(
    path.join(configDir, ".mcp.json"),
    JSON.stringify({ mcpServers: {
      unrelated: { command: "other", args: [] },
      "research-report-loop": { command: "old", args: [] },
    } }),
  );

  try {
    const completed = spawnSync(
      process.execPath,
      [
        path.join(root, "scripts/register-workbuddy-local.mjs"),
        configDir,
        "research-report-loop-memory@test-marketplace",
        installPath,
        "1.0.0-test",
        "test-marketplace",
        marketplaceRoot,
      ],
      { encoding: "utf8" },
    );
    assert.equal(completed.status, 0, completed.stderr);
    const settings = JSON.parse(fs.readFileSync(path.join(configDir, "settings.json"), "utf8"));
    assert.deepEqual(settings.sandbox.extraAllowWrite, ["/already-allowed"]);
    const mcp = JSON.parse(fs.readFileSync(path.join(configDir, ".mcp.json"), "utf8"));
    assert.equal(mcp.mcpServers.unrelated.command, "other");
    assert.equal(mcp.mcpServers["research-report-loop"], undefined);
    assert.equal(mcp.mcpServers["report-memory-v2"].command, process.platform === "win32" ? "cmd.exe" : "sh");
    assert.match(JSON.stringify(mcp.mcpServers["report-memory-v2"]), new RegExp(installPath.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&"), "u"));
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
