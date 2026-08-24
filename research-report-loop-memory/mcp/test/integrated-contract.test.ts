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
    input: JSON.stringify(input),
    encoding: "utf8",
    env: {
      ...process.env,
      RESEARCH_REPORT_MEMORY_GUARD_DIR: stateDir,
      RESEARCH_REPORT_MEMORY_WRITING_RECALL: "off",
    },
  });
  assert.equal(run.status, 0, run.stderr);
  return JSON.parse(run.stdout);
}

test("integrated Skill resolves a versioned Rubric Set instead of writing Recall", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const orchestration = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  assert.match(skill, /report_loop_start.*Base → core → audience → project/su);
  assert.match(skill, /写作前不要另行调用 `writing_memory_recall`/u);
  assert.match(skill, /Judge 的分数、反馈和自动改写.*绝不能触发 Capture/su);
  assert.match(orchestration, /写作前不执行 Memory Recall/u);
  assert.match(orchestration, /直接修改当前报告.*operation=capture.*再交付或总结/su);
  assert.match(skill, /先直接修改当前报告文件.*不重新启动或提交 Report Loop/su);
  assert.match(skill, /主 Agent 不得直接调用 Memory MCP/u);
  assert.match(skill, /不得因写作反馈而修改.*skills\/\*\*.*rubrics\/\*\*/su);
  assert.match(skill, /不得自行修补安装目录.*重启 Connector/su);
  assert.match(orchestration, /不得把 `~\/\.workbuddy\/MEMORY\.md`.*备用写入通道/su);
});

test("integrated Memory Hook allows drafting but still gates user feedback Capture", (t) => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "loop-memory-guard-"));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  const sessionId = "integrated-hook";
  const prompt = runHook(stateDir, "prompt", {
    session_id: sessionId,
    prompt: "帮我写一份面向管理层的战略研究报告",
  });
  assert.doesNotMatch(prompt.systemMessage ?? "", /MEMORY_RECALL_COMPLETED/u);
  const write = runHook(stateDir, "pre-tool", {
    session_id: sessionId,
    tool_name: "Write",
  });
  assert.equal(write.continue, true);

  const feedback = runHook(stateDir, "prompt", {
    session_id: sessionId,
    prompt: "这份报告不要铺陈背景，结论要更直接",
  });
  assert.match(feedback.systemMessage, /operation=capture/u);
  assert.match(feedback.systemMessage, /先直接修改当前报告文件/u);
  const earlyCapture = runHook(stateDir, "pre-tool", {
    session_id: sessionId,
    tool_name: "Agent",
    tool_input: { subagent_type: "research-report-memory-curator", operation: "capture" },
  });
  assert.equal(earlyCapture.hookSpecificOutput?.permissionDecision, "deny");
  runHook(stateDir, "post-tool", {
    session_id: sessionId,
    tool_name: "Edit",
    tool_input: { file_path: "/tmp/report.md" },
    tool_response: { status: "ok" },
  });
  const captureAfterRewrite = runHook(stateDir, "pre-tool", {
    session_id: sessionId,
    tool_name: "Agent",
    tool_input: { subagent_type: "research-report-memory-curator", operation: "capture" },
  });
  assert.equal(captureAfterRewrite.continue, true);
  const stop = runHook(stateDir, "stop", { session_id: sessionId });
  assert.equal(stop.hookSpecificOutput?.permissionDecision, "deny");
});

test("integrated manifests register two MCP servers and one Curator", () => {
  const plugin = JSON.parse(
    fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
  );
  assert.equal(plugin.name, "research-report-loop-memory");
  assert.deepEqual(
    Object.keys(plugin.mcpServers).sort(),
    ["research-report-loop", "research-report-memory-v2-0821"],
  );
  assert.deepEqual(plugin.agents, ["./agents/research-report-memory-curator.md"]);
});
