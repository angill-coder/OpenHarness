import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const hookPath = path.join(root, "hooks/capture-checkpoint.mjs");

function invoke(mode: string, payload: Record<string, unknown>, stateDir: string) {
  const result = spawnSync(process.execPath, ["--import", "tsx", hookPath, mode], {
    cwd: root,
    env: { ...process.env, RESEARCH_REPORT_CAPTURE_HOOK_DIR: stateDir },
    input: JSON.stringify(payload),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test("hook only checks feedback capture and delegates to the dedicated Curator", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "capture-flow";

  const initial = invoke("prompt", { session_id, prompt: "请根据材料写一份研究报告" }, stateDir);
  assert.equal(initial.systemMessage, undefined);
  assert.equal(
    invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision,
    "allow",
  );

  invoke("post-tool", {
    session_id,
    tool_name: "Skill",
    tool_input: { skill: "research-report-loop" },
    tool_response: { status: "ok" },
  }, stateDir);
  invoke("post-tool", {
    session_id,
    tool_name: "DeferExecuteTool",
    tool_input: { toolName: "mcp__research-report-loop__report_loop_finish" },
    tool_response: { structuredContent: { status: "completed" } },
  }, stateDir);

  const feedback = invoke("prompt", {
    session_id,
    prompt: "这份报告的摘要太长了，控制在两到三行，并直接给结论。",
  }, stateDir);
  assert.match(feedback.systemMessage, /先落实当前报告修改/u);
  assert.match(feedback.systemMessage, /Agent\/Task 委派 research-report-memory-curator/u);
  assert.match(feedback.systemMessage, /不是 WorkBuddy MEMORY\.md/u);
  assert.equal(
    invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision,
    "deny",
  );

  invoke("post-tool", {
    session_id,
    tool_name: "Task",
    tool_input: { subagent_type: "research-report-memory-curator", operation: "capture" },
    tool_response: "MEMORY_CAPTURE_COMPLETED",
  }, stateDir);
  assert.equal(
    invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision,
    "allow",
  );
});

test("explicit Curator failure releases the checkpoint", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-failure-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "capture-failure";
  invoke("post-tool", {
    session_id,
    tool_name: "Skill",
    tool_input: { skill: "research-report-loop" },
    tool_response: { status: "ok" },
  }, stateDir);
  invoke("prompt", {
    session_id,
    prompt: "以后所有正式报告的摘要都控制在两到三行。",
  }, stateDir);
  invoke("post-tool", {
    session_id,
    tool_name: "Agent",
    tool_input: { name: "research-report-memory-curator", operation: "capture" },
    tool_response: "MEMORY_CAPTURE_FAILED: connector unavailable",
  }, stateDir);
  assert.equal(
    invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision,
    "allow",
  );
});

test("report context corrections trigger Curator without forcing a rewrite", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-context-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "capture-context";
  invoke("post-tool", {
    session_id,
    tool_name: "Skill",
    tool_input: { skill: "research-report-loop" },
    tool_response: { status: "ok" },
  }, stateDir);
  const correction = invoke("prompt", {
    session_id,
    prompt: "A 和决策委员会 A 都是指同一位决策者。",
  }, stateDir);
  assert.match(correction.systemMessage, /报告相关背景或实体纠正/u);
  assert.match(correction.systemMessage, /无需为纯背景纠正改写报告/u);
  assert.match(correction.systemMessage, /research-report-memory-curator/u);
});

test("hook manifest contains no PreToolUse or report-loop/file gate", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "hooks/hooks.json"), "utf8"));
  assert.deepEqual(Object.keys(manifest.hooks).sort(), ["PostToolUse", "Stop", "UserPromptSubmit"]);
  assert.equal(JSON.stringify(manifest).includes("PreToolUse"), false);
  const source = fs.readFileSync(hookPath, "utf8");
  assert.doesNotMatch(source, /present_files|artifactPath|snapshotRevision/u);
});
