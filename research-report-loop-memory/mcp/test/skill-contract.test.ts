import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("integrated Skill writes first and delegates feedback capture", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const orchestration = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  assert.match(skill, /先直接修改当前报告[\s\S]*operation=capture/u);
  assert.match(skill, /写作前不要 Recall Memory/u);
  assert.match(orchestration, /operation=capture/u);
  assert.match(orchestration, /主 Agent.*不直接调用 Memory MCP/u);
  assert.match(orchestration, /不得修改.*Base Rubric/su);
  assert.match(orchestration, /MEMORY_CAPTURE_COMPLETED.*MEMORY_CAPTURE_FAILED/su);
});

test("Curator keeps the current Layer, Scope, and single-capture contract", () => {
  const agent = fs.readFileSync(path.join(root, "agents/research-report-memory-curator.md"), "utf8");
  const toolLine = agent.match(/^tools:.*$/mu)?.[0] ?? "";
  assert.match(toolLine, /mcp__report-memory-v2__writing_memory_recall/u);
  assert.match(toolLine, /writing_memory_capture_payload.*writing_memory_forget/u);
  assert.doesNotMatch(toolLine, /v2-mvp|ToolSearch|DeferExecuteTool/u);

  assert.match(agent, /记忆结构：Layer × Scope/u);
  assert.match(agent, /Scope 为 `core \/ audience \/ project` 三选一；Layer 不是三选一/u);
  assert.match(agent, /L0 Writing Episode/u);
  assert.match(agent, /L1 Atom Memory/u);
  assert.match(agent, /L2B Memory Rubrics/u);
  assert.doesNotMatch(agent, /L2 Context Memory|L3 Rubrics Memory/u);
  assert.match(agent, /不要使用固定命中次数、打分阈值/u);
  assert.match(agent, /保持 L2B 不变是正常/u);
  assert.match(agent, /换项目、换受众仍成立/u);
  assert.match(agent, /当前任务的受众和项目不能反推 Scope/u);
  assert.match(agent, /purpose=review, query=<当前反馈>, includeL1=true/u);
  assert.match(agent, /只调用一次；失败时只按明确错误修正一次/u);
  assert.match(agent, /"atoms": \[/u);
  assert.match(agent, /"rubricPatches": \[/u);
  assert.match(agent, /sourceRefs:\s*\["new:<operationRef>"\]/u);
  assert.match(agent, /action=update\|merge.*targetIds/u);
  assert.match(agent, /MEMORY_CAPTURE_COMPLETED.*MEMORY_CAPTURE_FAILED/su);
});

test("plugin and reflection use the report-memory-v2 service identifier", () => {
  const plugin = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
  const codexPlugin = JSON.parse(fs.readFileSync(path.join(root, ".codex-plugin/plugin.json"), "utf8"));
  const marketplace = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/marketplace.json"), "utf8"));
  const packageManifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  assert.equal(plugin.name, "research-report-loop-memory");
  assert.deepEqual(plugin.agents, [
    "./agents/research-report-memory-curator.md",
    "./agents/research-report-memory-reflection.md",
  ]);
  assert.equal(plugin.mcpServers, "./.mcp.json");
  assert.equal(plugin.version, packageManifest.version);
  assert.equal(codexPlugin.version, packageManifest.version);
  assert.equal(marketplace.plugins[0].version, packageManifest.version);
  const mcp = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  assert.equal(mcp.mcpServers["report-memory-v2"].command, "sh");
  assert.equal(
    mcp.mcpServers["report-memory-v2"].env.RESEARCH_REPORT_MEMORY_V2_0821_DIR,
    "~/.research-report-memory-v2-0821",
  );

  const script = fs.readFileSync(path.join(root, "scripts/run-memory-reflection-workbuddy.sh"), "utf8");
  assert.match(script, /RESEARCH_REPORT_MEMORY_V2_0821_DIR/u);
  assert.match(script, /report-memory-v2/u);
  assert.match(script, /research-report-memory-reflection\.md/u);
  assert.match(script, /purpose=reflection/u);
  assert.doesNotMatch(script, /research-report-memory-v2-mvp|RESEARCH_REPORT_MEMORY_V2_DIR/u);
});
