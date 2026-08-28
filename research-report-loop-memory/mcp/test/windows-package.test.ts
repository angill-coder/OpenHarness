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

test("Reflection schedules resolve the currently installed plugin on both platforms", () => {
  const windowsInstaller = fs.readFileSync(path.join(root, "scripts/install-reflection-windows.ps1"), "utf8");
  const windowsLauncher = fs.readFileSync(path.join(root, "scripts/reflection-current.ps1"), "utf8");
  const windowsReflection = fs.readFileSync(path.join(root, "scripts/run-memory-reflection-workbuddy.ps1"), "utf8");
  const macInstaller = fs.readFileSync(path.join(root, "scripts/install-reflection-macos.sh"), "utf8");
  const macLauncher = fs.readFileSync(path.join(root, "scripts/reflection-current.sh"), "utf8");
  const macReflection = fs.readFileSync(path.join(root, "scripts/run-memory-reflection-workbuddy.sh"), "utf8");
  const windowsDisable = fs.readFileSync(path.join(root, "scripts/disable-reflection-windows.ps1"), "utf8");
  const macDisable = fs.readFileSync(path.join(root, "scripts/disable-reflection-macos.sh"), "utf8");

  assert.match(windowsInstaller, /reflection-current\.ps1/u);
  assert.match(windowsInstaller, /RESEARCH_REPORT_MEMORY_V2_0821_DIR/u);
  assert.match(windowsLauncher, /installed_plugins\.json/u);
  assert.match(windowsLauncher, /\$Registry\.plugins/u);
  assert.match(windowsLauncher, /\$Plugins\.\$ExactKey/u);
  assert.match(windowsLauncher, /\$Plugins\.PSObject\.Properties/u);
  assert.doesNotMatch(windowsLauncher, /\$Registry\.\$ExactKey/u);
  assert.match(windowsLauncher, /research-report-loop-memory@/u);
  assert.match(windowsLauncher, /run-memory-reflection-workbuddy\.ps1/u);
  assert.match(windowsReflection, /Join-Path \$WorkBuddyConfig "binaries\\node\\versions"/u);
  assert.match(windowsReflection, /WORKBUDDY_EXTRA_PATHS/u);
  assert.match(windowsReflection, /CODEBUDDY_CODE_PATH/u);
  assert.match(windowsReflection, /ProgramFiles\(x86\)/u);
  assert.match(windowsReflection, /args = @\("\/d", "\/c", \$NodeRunner, \$MemoryServer\)/u);
  assert.match(windowsReflection, /\[Console\]::InputEncoding = \$Utf8NoBom/u);
  assert.match(windowsReflection, /\[Console\]::OutputEncoding = \$Utf8NoBom/u);
  assert.match(windowsReflection, /\$OutputEncoding = \$Utf8NoBom/u);
  assert.match(windowsReflection, /Get-Content -Raw -Encoding utf8/u);
  assert.match(windowsReflection, /Execute operation=reflection/u);
  assert.match(windowsReflection, /settings\.json/u);
  assert.match(windowsReflection, /memoryEnabled/u);
  assert.doesNotMatch(windowsReflection, /[^\x00-\x7F]/u);
  assert.doesNotMatch(windowsReflection, /"\/s"|\$CommandLine/u);
  assert.match(macInstaller, /reflection-current\.sh/u);
  assert.match(macInstaller, /DATA_DIR=/u);
  assert.match(macInstaller, /EnvironmentVariables\.WORKBUDDY_CONFIG_DIR/u);
  assert.match(macInstaller, /EnvironmentVariables\.WORKBUDDY_CODEBUDDY/u);
  assert.match(macInstaller, /CODEBUDDY_CODE_PATH/u);
  assert.match(macLauncher, /installed_plugins\.json/u);
  assert.match(macLauncher, /WORKBUDDY_EXTRA_PATHS/u);
  assert.match(macLauncher, /WORKBUDDY_CONFIG_DIR:-\$\{CODEBUDDY_CONFIG_DIR/u);
  assert.match(macReflection, /CODEBUDDY_CODE_PATH/u);
  assert.match(macReflection, /WORKBUDDY_EXTRA_PATHS/u);
  assert.match(macReflection, /\/Volumes\/\*\/Applications\/WorkBuddy\.app/u);
  assert.match(macReflection, /settings\.json/u);
  assert.match(macReflection, /memoryEnabled/u);
  assert.match(windowsInstaller, /schedule-settings\.json/u);
  assert.match(windowsDisable, /enabled = \$false/u);
  assert.match(macInstaller, /schedule-settings\.json/u);
  assert.match(macDisable, /"enabled":false/u);
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
  assert.match(builder, /gpt-5\.6-sol/u);
  assert.match(builder, /reflection-current\.ps1/u);
  assert.match(builder, /defaultJudgeProvider: "workbuddy"/u);
  assert.match(builder, /judgeDefaults/u);
  assert.doesNotMatch(builder, /--judge-provider/u);
  assert.match(builder, /mcp\/report_loop/u);
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
    `research-report-loop-memory-${version}-win32-x64-hook-test`,
    "plugins/research-report-loop-memory",
  );
  const mcp = JSON.parse(fs.readFileSync(path.join(pluginDir, ".mcp.json"), "utf8"));
  assert.equal(mcp.mcpServers["report-memory-v2"].command, "cmd.exe");
  assert.deepEqual(mcp.mcpServers["report-memory-v2"].args, [
    "/d",
    "/c",
    "${CODEBUDDY_PLUGIN_ROOT}\\scripts\\run-node.cmd",
    "${CODEBUDDY_PLUGIN_ROOT}\\dist\\memory-server.mjs",
  ]);
  const hooks = JSON.parse(fs.readFileSync(path.join(pluginDir, "hooks/hooks.json"), "utf8"));
  for (const registrations of Object.values(hooks.hooks) as Array<Array<{ hooks: Array<{ command: string }> }>>) {
    const command = registrations[0].hooks[0].command;
    assert.match(command, /^cmd\.exe \/d \/c call "\$\{CODEBUDDY_PLUGIN_ROOT\}\\scripts\\run-node\.cmd" /u);
    assert.match(command, /"\$\{CODEBUDDY_PLUGIN_ROOT\}\\dist\\capture-checkpoint\.mjs" (?:prompt|post-tool|stop)$/u);
    assert.doesNotMatch(command, /\/s|""|\bsh\b|run-node\.sh/u);
  }
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
  fs.mkdirSync(path.join(installPath, "dist"), { recursive: true });
  fs.writeFileSync(path.join(installPath, "dist/memory-server.mjs"), "// test server\n");
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
    const cacheInstallPath = path.join(
      configDir,
      "plugins/cache/test-marketplace/research-report-loop-memory/1.0.0-test",
    );
    if (process.platform === "win32") {
      assert.deepEqual(mcp.mcpServers["report-memory-v2"].args, [
        "/d",
        "/c",
        path.join(cacheInstallPath, "scripts/run-node.cmd"),
        path.join(cacheInstallPath, "dist/memory-server.mjs"),
      ]);
    } else {
      assert.deepEqual(mcp.mcpServers["report-memory-v2"].args, [
        path.join(cacheInstallPath, "scripts/run-node.sh"),
        path.join(cacheInstallPath, "dist/memory-server.mjs"),
      ]);
    }
    assert.equal(fs.readFileSync(path.join(cacheInstallPath, "dist/memory-server.mjs"), "utf8"), "// test server\n");
    const installed = JSON.parse(fs.readFileSync(path.join(configDir, "plugins/installed_plugins.json"), "utf8"));
    assert.equal(
      installed.plugins["research-report-loop-memory@test-marketplace"][0].installPath,
      cacheInstallPath,
    );
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
