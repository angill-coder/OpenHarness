import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const hook = path.join(root, "hooks/memory-guard.mjs");
const loader = path.join(root, "node_modules/tsx/dist/loader.mjs");

function runHook(stateDir: string, mode: string, input: Record<string, unknown>) {
  const run = spawnSync(process.execPath, ["--import", loader, hook, mode], {
    input: JSON.stringify(input), encoding: "utf8",
    env: { ...process.env, RESEARCH_REPORT_MEMORY_GUARD_DIR: stateDir },
  });
  assert.equal(run.status, 0, run.stderr);
  return JSON.parse(run.stdout);
}

function stopDecision(result: any) {
  const value = result.hookSpecificOutput?.permissionDecision;
  return value === "deny" ? "block" : value === "allow" ? "allow" : undefined;
}

function completeRecall(stateDir: string, sessionId: string) {
  return runHook(stateDir, "post-tool", {
    session_id: sessionId,
    tool_name: "Agent",
    tool_input: { subagent_type: "research-report-memory-curator", operation: "recall" },
    tool_response: { content: "MEMORY_RECALL_COMPLETED\n<research-report-memory />" },
  });
}

function completeCapture(stateDir: string, sessionId: string) {
  return runHook(stateDir, "post-tool", {
    session_id: sessionId,
    tool_name: "Agent",
    tool_input: { subagent_type: "research-report-memory-curator", operation: "capture" },
    tool_response: { content: "MEMORY_CAPTURE_COMPLETED status=stored" },
  });
}

test("Guard requires WB Memory Sub-agent recall before drafting", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-recall";
  const prompt = runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份面向管理层的战略分析报告" });
  assert.match(prompt.systemMessage, /research-report-memory-curator/u);
  assert.match(prompt.systemMessage, /MEMORY_RECALL_COMPLETED/u);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).hookSpecificOutput.permissionDecision, "deny");

  // 主 Agent 直接调 MCP 不算完成，必须有 Sub-agent 完成标记。
  runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "mcp__research-report-memory-v2-mvp__writing_memory_recall",
    tool_response: { status: "ok" },
  });
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).hookSpecificOutput.permissionDecision, "deny");
  completeRecall(stateDir, session_id);
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).continue, true);
});

test("Guard blocks a progress-only stop after intake answers return", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-intake-complete";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份面向管理层的战略分析报告" });

  const intake = runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "AskUserQuestion",
    tool_input: { questions: ["汇报背景", "材料假设", "重点素材"] },
    tool_response: { content: "管理委员会；验证双重驱动；structured_data.json 为重点" },
  });
  assert.match(intake.systemMessage, /不得停在.*准备 recall/su);

  const prematureStop = runHook(stateDir, "stop", { session_id });
  assert.equal(stopDecision(prematureStop), "block");
  assert.match(prematureStop.reason, /不要只说明.*准备召回/su);
  assert.match(prematureStop.systemMessage, /Memory Sub-agent 委派/u);

  completeRecall(stateDir, session_id);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");
});

test("Guard hardens recall after a follow-up clarification turn", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-text-intake";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");

  const followUp = runHook(stateDir, "prompt", {
    session_id,
    prompt: "汇报对象是管理委员会，验证频次和单次时长双重驱动，以 structured_data.json 为重点素材",
  });
  assert.match(followUp.systemMessage, /本轮不得只回复/u);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "block");
});

test("Guard allows cancelling a report while intake is pending", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-cancel-intake";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });
  runHook(stateDir, "prompt", { session_id, prompt: "先不用写了，取消这次任务" });
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).continue, true);
});

test("Guard requires WB Memory Sub-agent capture before delivery", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-capture";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });
  completeRecall(stateDir, session_id);
  const feedback = runHook(stateDir, "prompt", { session_id, prompt: "这份报告不要铺陈背景，结论要更直接" });
  assert.match(feedback.systemMessage, /research-report-memory-curator/u);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "block");
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "present_files" }).hookSpecificOutput.permissionDecision, "deny");
  assert.equal(runHook(stateDir, "pre-tool", {
    session_id,
    tool_name: "DeferExecuteTool",
    tool_input: { toolName: "mcp__research-report-memory-v2-mvp__writing_memory_capture", params: {} },
  }).continue, true);

  runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "writing_memory_capture",
    tool_response: { status: "stored", stored: true },
  });
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "block");
  completeCapture(stateDir, session_id);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");
});

test("Fresh revision feedback requires capture without arming new-report recall", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-fresh-revision";
  const feedback = runHook(stateDir, "prompt", {
    session_id,
    prompt: "这份报告正文太简洁了，把分析推导展开一些",
  });
  assert.match(feedback.systemMessage, /operation=capture/u);
  assert.doesNotMatch(feedback.systemMessage, /MEMORY_RECALL_COMPLETED/u);
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).continue, true);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "block");
  completeCapture(stateDir, session_id);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");
});

test("Skill activation after section-only revision keeps recall disabled", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-section-revision-before-skill";
  runHook(stateDir, "prompt", { session_id, prompt: "摘要太长了，压缩到三行" });
  const activation = runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "Skill",
    tool_input: { skill: "research-report" },
    tool_response: { status: "ok" },
  });
  assert.match(activation.systemMessage, /本轮不重新触发写前 recall/u);
  assert.match(activation.systemMessage, /operation=capture/u);
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Edit" }).continue, true);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "block");
});

test("A report revision about memory remains writing feedback", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-report-about-memory";
  const feedback = runHook(stateDir, "prompt", {
    session_id,
    prompt: "这份报告里的 memory 分类写得太复杂了，改成三类",
  });
  assert.match(feedback.systemMessage, /operation=capture/u);
  assert.doesNotMatch(feedback.systemMessage, /MEMORY_RECALL_COMPLETED/u);
});

test("Explicit new-report work still requires recall", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-explicit-new-report";
  const prompt = runHook(stateDir, "prompt", {
    session_id,
    prompt: "请重新写一份新的战略研究报告，摘要控制在三行",
  });
  assert.match(prompt.systemMessage, /MEMORY_RECALL_COMPLETED/u);
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).hookSpecificOutput.permissionDecision, "deny");
});

test("Revision language cannot clear recall already armed by a new report", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-revision-after-new-report";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份战略研究报告" });
  runHook(stateDir, "prompt", { session_id, prompt: "摘要太长了，控制在三行" });
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).hookSpecificOutput.permissionDecision, "deny");
});

test("Explicit Memory administration does not arm ordinary feedback capture", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-explicit-memory-manage";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });
  completeRecall(stateDir, session_id);
  const manage = runHook(stateDir, "prompt", {
    session_id,
    prompt: "把这条写作记忆从 audience 重新分类到 core",
  });
  assert.equal(manage.systemMessage, undefined);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");
});

test("Guard fails open only after an explicit Curator capture failure", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-capture-failure";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });
  completeRecall(stateDir, session_id);
  runHook(stateDir, "prompt", { session_id, prompt: "这份报告不要单独开归因章节" });

  const failed = runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "Agent",
    tool_input: { subagent_type: "research-report-memory-curator", operation: "capture" },
    tool_response: { content: "MEMORY_CAPTURE_FAILED reason=writing_episode_required" },
  });
  assert.match(failed.systemMessage, /允许继续交付/u);
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "present_files" }).continue, true);
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");
});

test("Guard fails open only after an explicit Curator recall failure", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-recall-failure";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });
  const failed = runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "Agent",
    tool_input: { subagent_type: "research-report-memory-curator", operation: "recall" },
    tool_response: { content: "MEMORY_RECALL_FAILED reason=mcp_unavailable" },
  });
  assert.match(failed.systemMessage, /按无个性化记忆继续写作/u);
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" }).continue, true);
});

test("Skill loading activates the guard even without prompt keyword matching", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-skill";
  runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "Skill",
    tool_input: { skill: "research-report" },
    tool_response: { status: "ok" },
  });
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Edit" }).hookSpecificOutput.permissionDecision, "deny");
  completeRecall(stateDir, session_id);
  assert.equal(runHook(stateDir, "pre-tool", { session_id, tool_name: "Edit" }).continue, true);
});

test("Guard does not inspect MCP caller identity or treat direct MCP output as completion", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-curator-caller";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });

  const directRecall = runHook(stateDir, "pre-tool", {
    session_id,
    tool_name: "mcp__research_report_memory_v2_mvp__writing_memory_recall",
    tool_input: { task: "研究报告", purpose: "writing" },
  });
  assert.equal(directRecall.continue, true);
  assert.equal(directRecall.hookSpecificOutput, undefined);

  runHook(stateDir, "post-tool", {
    session_id,
    tool_name: "mcp__research_report_memory_v2_mvp__writing_memory_recall",
    tool_response: { status: "ok", context: "" },
  });
  const parentWrite = runHook(stateDir, "pre-tool", { session_id, tool_name: "Write" });
  assert.equal(parentWrite.hookSpecificOutput.permissionDecision, "deny");
});

test("Non-writing feedback is ignored and re-entrant Stop fails open", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "memory-v2-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "session-safe";
  runHook(stateDir, "prompt", { session_id, prompt: "帮我写一份研究报告" });
  completeRecall(stateDir, session_id);
  runHook(stateDir, "prompt", { session_id, prompt: "我喜欢吃米饭" });
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id })), "allow");

  runHook(stateDir, "prompt", { session_id, prompt: "报告的表达要更简洁" });
  assert.equal(stopDecision(runHook(stateDir, "stop", { session_id, stop_hook_active: false })), "block");
  const reentrant = runHook(stateDir, "stop", { session_id, stop_hook_active: true });
  assert.equal(reentrant.continue, true);
});
