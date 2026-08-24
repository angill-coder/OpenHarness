import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("Windows launchers discover WorkBuddy runtimes and force UTF-8", () => {
  const nodeRunner = fs.readFileSync(path.join(root, "scripts/run-node.cmd"), "utf8");
  const pythonRunner = fs.readFileSync(path.join(root, "scripts/run-python.cmd"), "utf8");
  const reportServer = fs.readFileSync(path.join(root, "mcp/report_loop/server.py"), "utf8");

  assert.match(nodeRunner, /WORKBUDDY_NODE/u);
  assert.match(nodeRunner, /\.workbuddy\\binaries\\node\\versions/u);
  assert.match(nodeRunner, /node\.exe/u);
  assert.match(nodeRunner, /22\.16/u);
  assert.match(pythonRunner, /WORKBUDDY_PYTHON/u);
  assert.match(pythonRunner, /\.workbuddy\\binaries\\python\\versions/u);
  assert.match(pythonRunner, /PYTHONUTF8=1/u);
  assert.match(pythonRunner, /PYTHONIOENCODING=utf-8/u);
  assert.match(reportServer, /reconfigure\(encoding="utf-8"/u);
});

test("release builder emits a Windows-native MCP and Hook configuration", () => {
  const builder = fs.readFileSync(path.join(root, "scripts/build-release.mjs"), "utf8");
  assert.match(builder, /--target-platform/u);
  assert.match(builder, /targetPlatform === "win32"/u);
  assert.match(builder, /command: "cmd\.exe"/u);
  assert.match(builder, /run-python\.cmd/u);
  assert.match(builder, /run-node\.cmd/u);
  assert.match(builder, /capture-checkpoint\.mjs/u);
});

test("package preflight checks the stable Report Loop and Memory tool contracts", () => {
  const preflight = fs.readFileSync(path.join(root, "scripts/verify-mcp-contract.mjs"), "utf8");
  for (const tool of [
    "report_loop_start",
    "report_loop_submit",
    "report_loop_finish",
    "report_loop_status",
    "writing_memory_recall",
    "writing_memory_capture_payload",
  ]) {
    assert.match(preflight, new RegExp(tool, "u"));
  }
  assert.doesNotMatch(preflight, /report_loop_(?:create|generate|judge|get)["']/u);
});
