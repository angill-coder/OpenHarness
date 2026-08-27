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
    env: {
      ...process.env,
      RESEARCH_REPORT_CAPTURE_HOOK_DIR: stateDir,
      RESEARCH_REPORT_MEMORY_V2_0821_DIR: path.join(stateDir, "memory"),
      RESEARCH_REPORT_LOOP_HOOK_NO_SPAWN: "1",
    },
    input: JSON.stringify(payload),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

function enableMemory(stateDir: string) {
  const dataDir = path.join(stateDir, "memory");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(
    path.join(dataDir, "settings.json"),
    JSON.stringify({ schemaVersion: 1, memoryEnabled: true }),
  );
}

test("hook only checks feedback capture and delegates to the dedicated Curator", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  enableMemory(stateDir);
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
  enableMemory(stateDir);
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

test("markerless Curator result fails open instead of retrying forever", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-markerless-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  enableMemory(stateDir);
  const session_id = "capture-markerless";
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
  const result = invoke("post-tool", {
    session_id,
    tool_name: "Agent",
    tool_input: { name: "research-report-memory-curator", operation: "capture" },
    tool_response: "当前会话搜索不到 report-memory-v2 MCP 工具。",
  }, stateDir);
  assert.match(result.systemMessage, /按失败放行/u);
  assert.match(result.systemMessage, /不要重复委派/u);
  assert.equal(
    invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision,
    "allow",
  );
});

test("recursive Stop invocation releases an unavailable capture checkpoint", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-stop-once-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  enableMemory(stateDir);
  const session_id = "capture-stop-once";
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
  assert.equal(invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision, "deny");
  const released = invoke("stop", { session_id, stop_hook_active: true }, stateDir);
  assert.equal(released.hookSpecificOutput.permissionDecision, "allow");
  assert.match(released.systemMessage, /只检查一次/u);
  assert.equal(invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision, "allow");
});

test("report context corrections trigger Curator without forcing a rewrite", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-context-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  enableMemory(stateDir);
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

test("memory capture is active by default", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-default-enabled-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const session_id = "capture-default-enabled";

  invoke("post-tool", {
    session_id,
    tool_name: "Skill",
    tool_input: { skill: "research-report-loop" },
    tool_response: { status: "ok" },
  }, stateDir);
  const feedback = invoke("prompt", {
    session_id,
    prompt: "以后所有正式报告的摘要都控制在两到三行。",
  }, stateDir);

  assert.match(feedback.systemMessage, /research-report-memory-curator/u);
  assert.equal(invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision, "deny");
});

test("an explicit memory disable suppresses capture", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-explicit-disabled-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const dataDir = path.join(stateDir, "memory");
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(
    path.join(dataDir, "settings.json"),
    JSON.stringify({ schemaVersion: 1, memoryEnabled: false }),
  );
  const session_id = "capture-explicit-disabled";
  invoke("post-tool", {
    session_id,
    tool_name: "Skill",
    tool_input: { skill: "research-report-loop" },
    tool_response: { status: "ok" },
  }, stateDir);
  const feedback = invoke("prompt", {
    session_id,
    prompt: "以后所有正式报告的摘要都控制在两到三行。",
  }, stateDir);

  assert.equal(feedback.systemMessage, undefined);
  assert.equal(invoke("stop", { session_id }, stateDir).hookSpecificOutput.permissionDecision, "allow");
});

test("hook manifest contains no PreToolUse or report-loop/file gate", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "hooks/hooks.json"), "utf8"));
  assert.deepEqual(Object.keys(manifest.hooks).sort(), ["PostToolUse", "Stop", "UserPromptSubmit"]);
  assert.equal(JSON.stringify(manifest).includes("PreToolUse"), false);
  const source = fs.readFileSync(hookPath, "utf8");
  assert.doesNotMatch(source, /present_files|artifactPath|snapshotRevision/u);
});

test("successful Report Loop Job write stamps the session and queues the host launcher", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-session-"));
  const jobDir = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-job-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  t.after(() => fs.rmSync(jobDir, { recursive: true, force: true }));
  const jobPath = path.join(jobDir, "report_loop_job.json");
  fs.writeFileSync(jobPath, JSON.stringify({
    schemaVersion: 2,
    v1ArtifactPath: path.join(jobDir, "report-v1.md"),
    outputPath: path.join(jobDir, "report-final.md"),
  }));

  const response = invoke("post-tool", {
    session_id: "session-sidecar-0001",
    tool_name: "Write",
    tool_input: { file_path: jobPath },
    tool_response: { status: "completed" },
  }, stateDir);

  assert.deepEqual(
    JSON.parse(fs.readFileSync(`${jobPath}.session.json`, "utf8")),
    { version: 1, sessionId: "session-sidecar-0001" },
  );
  assert.match(response.systemMessage, /插件 Hook 在宿主侧启动/u);
  assert.match(response.systemMessage, /后台等待命令/u);
  assert.match(response.systemMessage, /wait-loop-result/u);
  assert.match(response.systemMessage, /run_in_background=true/u);
  assert.match(response.systemMessage, /TaskOutput\(task_id\)/u);
  assert.match(response.systemMessage, /<task-notification>/u);
  assert.match(response.systemMessage, /不要搜索或调用 report_loop_run/u);
  const statusFiles = fs.readdirSync(jobDir).filter((name) => name.endsWith(".status.json"));
  assert.equal(statusFiles.length, 1);
  const status = JSON.parse(fs.readFileSync(path.join(jobDir, statusFiles[0]), "utf8"));
  assert.equal(status.state, "queued");
  assert.equal(status.jobPath, jobPath);
  assert.equal(path.isAbsolute(status.resultPath), true);

  const blocked = invoke("stop", { session_id: "session-sidecar-0001" }, stateDir);
  assert.equal(blocked.hookSpecificOutput.permissionDecision, "deny");
  assert.match(blocked.hookSpecificOutput.permissionDecisionReason, /仍在后台运行/u);

  const completed = invoke("post-tool", {
    session_id: "session-sidecar-0001",
    tool_name: "TaskOutput",
    tool_input: { task_id: "task-1" },
    tool_response: {
      status: "completed",
      output: JSON.stringify({
        status: "completed",
        finalArtifactPath: path.join(jobDir, "report-final.md"),
        versionsDirectory: path.join(jobDir, "report-final-versions"),
        judgedVersions: 3,
        rewriteRounds: 2,
        bestVersion: "v3",
        bestScore: 5,
      }),
    },
  }, stateDir);
  assert.match(completed.systemMessage, /Report Loop 已完成/u);
  assert.match(completed.systemMessage, /不要展示原始 JSON/u);
  assert.equal(
    invoke("stop", { session_id: "session-sidecar-0001" }, stateDir).hookSpecificOutput.permissionDecision,
    "allow",
  );
});

test("background waiter returns a completed Report Loop result", (t) => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-waiter-"));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const resultPath = path.join(temporary, "result.json");
  const statusPath = path.join(temporary, "status.json");
  const expected = { status: "completed", finalArtifactPath: path.join(temporary, "final.md") };
  fs.writeFileSync(resultPath, `${JSON.stringify(expected)}\n`);
  fs.writeFileSync(statusPath, JSON.stringify({ state: "completed" }));

  const completed = spawnSync(
    process.execPath,
    ["--import", "tsx", hookPath, "wait-loop-result", resultPath, statusPath],
    { cwd: root, encoding: "utf8" },
  );
  assert.equal(completed.status, 0, completed.stderr);
  assert.deepEqual(JSON.parse(completed.stdout), expected);
});

test("the same Job write is idempotent and reuses one result path", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-idempotent-"));
  const jobDir = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-idempotent-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  t.after(() => fs.rmSync(jobDir, { recursive: true, force: true }));
  const jobPath = path.join(jobDir, "report-loop-job.json");
  fs.writeFileSync(jobPath, JSON.stringify({
    schemaVersion: 2,
    v1ArtifactPath: path.join(jobDir, "report-v1.md"),
    outputPath: path.join(jobDir, "report-final.md"),
  }));
  const payload = {
    session_id: "session-idempotent",
    tool_name: "Write",
    tool_input: { file_path: jobPath },
    tool_response: { status: "completed" },
  };
  const first = invoke("post-tool", payload, stateDir);
  const second = invoke("post-tool", payload, stateDir);
  const firstResult = first.systemMessage.match(/结果文件：(.+?)。/u)?.[1];
  const secondResult = second.systemMessage.match(/结果文件：(.+?)。/u)?.[1];
  assert.equal(firstResult, secondResult);
  assert.equal(fs.readdirSync(jobDir).filter((name) => name.endsWith(".lock")).length, 1);
});

test("identical Jobs with different filenames share one launch and result path", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-hook-content-idempotent-"));
  const jobDir = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-content-idempotent-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  t.after(() => fs.rmSync(jobDir, { recursive: true, force: true }));
  const job = {
    schemaVersion: 2,
    v1ArtifactPath: path.join(jobDir, "report-v1.md"),
    outputPath: path.join(jobDir, "report-final.md"),
  };
  const firstJobPath = path.join(jobDir, "report-loop-job-ds-duration.json");
  const secondJobPath = path.join(jobDir, "report-loop-job.json");
  fs.writeFileSync(firstJobPath, JSON.stringify(job));
  fs.writeFileSync(secondJobPath, JSON.stringify(job));

  const first = invoke("post-tool", {
    session_id: "session-content-idempotent",
    tool_name: "Write",
    tool_input: { file_path: firstJobPath },
    tool_response: { status: "completed" },
  }, stateDir);
  const second = invoke("post-tool", {
    session_id: "session-content-idempotent",
    tool_name: "Write",
    tool_input: { file_path: secondJobPath },
    tool_response: { status: "completed" },
  }, stateDir);

  const firstResult = first.systemMessage.match(/结果文件：(.+?)。/u)?.[1];
  const secondResult = second.systemMessage.match(/结果文件：(.+?)。/u)?.[1];
  assert.equal(firstResult, secondResult);
  assert.equal(fs.readdirSync(jobDir).filter((name) => name.endsWith(".lock")).length, 1);
  assert.equal(fs.readdirSync(jobDir).filter((name) => name.endsWith(".status.json")).length, 1);
});

test("host worker relays the Python Runner result without an MCP tool call", (t) => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-host-worker-"));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const scriptsDir = path.join(temporary, "scripts");
  const runnerDir = path.join(temporary, "mcp/report_loop");
  fs.mkdirSync(scriptsDir, { recursive: true });
  fs.mkdirSync(runnerDir, { recursive: true });
  const launcher = path.join(scriptsDir, process.platform === "win32" ? "run-python.cmd" : "run-python.sh");
  fs.writeFileSync(path.join(runnerDir, "runner.py"), "# test runner\n");
  if (process.platform === "win32") {
    fs.writeFileSync(launcher, "@echo off\r\necho {\"status\":\"completed\",\"finalArtifactPath\":\"report-final.md\"}\r\n");
  } else {
    fs.writeFileSync(launcher, "#!/bin/sh\nprintf '%s\\n' '{\"status\":\"completed\",\"finalArtifactPath\":\"report-final.md\"}'\n", { mode: 0o755 });
  }
  const jobPath = path.join(temporary, "job.json");
  const statusPath = path.join(temporary, "status.json");
  const resultPath = path.join(temporary, "result.json");
  const lockPath = path.join(temporary, "worker.lock");
  fs.writeFileSync(jobPath, JSON.stringify({ schemaVersion: 2 }));
  fs.writeFileSync(lockPath, "locked\n");
  const completed = spawnSync(
    process.execPath,
    ["--import", "tsx", hookPath, "run-loop-worker", jobPath, statusPath, resultPath, lockPath],
    {
      cwd: root,
      env: { ...process.env, CODEBUDDY_PLUGIN_ROOT: temporary },
      encoding: "utf8",
    },
  );
  assert.equal(completed.status, 0, completed.stderr);
  const result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
  assert.equal(result.status, "completed");
  assert.equal(result.finalArtifactPath, "report-final.md");
  assert.equal(fs.existsSync(lockPath), false);
});
