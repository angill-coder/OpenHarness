import assert from "node:assert/strict";
import fs from "node:fs";
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

test("release builder emits platform-native Memory MCP and Hook configurations", () => {
  const builder = fs.readFileSync(path.join(root, "scripts/build-release.mjs"), "utf8");
  assert.match(builder, /--target-platform/u);
  assert.match(builder, /targetPlatform === "win32"/u);
  assert.match(builder, /command: "cmd\.exe"/u);
  assert.match(builder, /run-node\.cmd/u);
  assert.match(builder, /command: "sh"/u);
  assert.match(builder, /run-node\.sh/u);
  assert.match(builder, /capture-checkpoint\.mjs/u);
  assert.match(builder, /deepseek-v4-pro/u);
  assert.doesNotMatch(builder, /gpt-5\.6-sol/u);
  assert.doesNotMatch(builder, /RESEARCH_REPORT_LOOP_CODEX_MODEL/u);
});

test("Report Loop runs only through the Python runner", () => {
  assert.equal(fs.existsSync(path.join(root, "mcp/report_loop/runner.py")), true);
  assert.equal(fs.existsSync(path.join(root, "mcp/report_loop/server.py")), false);
  assert.equal(fs.existsSync(path.join(root, "mcp/report_loop/core/codex_cli.py")), false);

  const sourceMcp = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  assert.deepEqual(Object.keys(sourceMcp.mcpServers), ["report-memory-v2"]);

  const preflight = fs.readFileSync(path.join(root, "scripts/verify-mcp-contract.mjs"), "utf8");
  assert.match(preflight, /writing_memory_recall/u);
  assert.match(preflight, /writing_memory_capture_payload/u);
  assert.doesNotMatch(preflight, /report_loop_(?:start|submit|finish|status)/u);
});
