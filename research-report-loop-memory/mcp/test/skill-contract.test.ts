import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("research-report delegates recall and capture without changing its writing instructions", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report/SKILL.md"), "utf8");
  const orchestration = fs.readFileSync(path.join(root, "skills/research-report/references/memory-orchestration.md"), "utf8");
  const instructions = fs.readFileSync(path.join(root, "skills/research-report/references/instructions.md"), "utf8");
  const upstreamSkill = fs.readFileSync(path.join(root, "upstream/research-report/SKILL.md"), "utf8");
  const upstreamInstructions = fs.readFileSync(path.join(root, "upstream/research-report/instructions.md"), "utf8");

  assert.match(skill, /references\/memory-orchestration\.md/u);
  assert.doesNotMatch(upstreamSkill, /memory-orchestration/u);
  assert.equal(instructions, upstreamInstructions);
  assert.match(orchestration, /三项需求澄清[\s\S]*operation=recall[\s\S]*MEMORY_RECALL_COMPLETED/u);
  assert.match(orchestration, /完成当前修改.*operation=capture.*再交付或总结/u);
  assert.match(orchestration, /主 Agent 不判断反馈应进入哪层/u);
  assert.match(orchestration, /普通报告反馈一律走 Capture/u);
  assert.match(orchestration, /Capture 期间不得改走 Manage 或先调用 Forget/u);
  assert.match(orchestration, /L0 原始对话窗口/u);
  assert.match(orchestration, /L2B Memory Rubrics.*写作和交付前自检/u);
  assert.match(orchestration, /本轮用户明确要求 > `project` > `audience` > `core` > 本 Skill/u);
  assert.match(orchestration, /MEMORY_RECALL_FAILED.*MEMORY_CAPTURE_FAILED/su);
});

test("Curator uses L0/L1/L2B with an insensitive evidence-based filter", () => {
  const agent = fs.readFileSync(path.join(root, "agents/research-report-memory-curator.md"), "utf8");
  const toolLine = agent.match(/^tools:.*$/mu)?.[0] ?? "";
  assert.match(toolLine, /mcp__research-report-memory-v2-0821__writing_memory_recall/u);
  assert.match(toolLine, /writing_memory_capture_payload.*writing_memory_forget/u);
  assert.doesNotMatch(toolLine, /v2-mvp|ToolSearch|DeferExecuteTool/u);

  assert.match(agent, /记忆结构：Layer × Scope/u);
  assert.match(agent, /Scope 是三选一；Layer 不是三选一/u);
  assert.match(agent, /L0 Writing Episode.*L1 Atom Memory.*L2B Memory Rubrics/su);
  assert.doesNotMatch(agent, /L2 Context Memory|L3 Rubrics Memory/u);
  assert.match(agent, /普通单次反馈通常停在 L1/u);
  assert.match(agent, /不要使用机械次数阈值/u);
  assert.match(agent, /多个相互独立的证据/u);
  assert.match(agent, /明确、强烈地要求它长期适用/u);
  assert.match(agent, /保持 L2B 不变是正常/u);
  assert.match(agent, /不要创建 `candidate` Rubric/u);
  assert.match(agent, /Good：三个独立项目/u);
  assert.match(agent, /Bad：用户只在当前一页/u);
  assert.match(agent, /traceability \/ structure \/ narrative \/ insight \/ coverage \/ expression/u);
  assert.match(agent, /redline: false.*status: active/su);

  assert.match(agent, /换项目、换受众仍成立/u);
  assert.match(agent, /只有因受众而改变才是 `audience`/u);
  assert.match(agent, /“这份报告”“这一版”.*不能证明 `project`/u);
  assert.match(agent, /当前任务的 Audience\/Project.*不能反推记忆 Scope/u);

  assert.match(agent, /purpose=review, query=<当前反馈>, includeL1=true/u);
  assert.match(agent, /只调用一次 `writing_memory_capture_payload`/u);
  assert.match(agent, /失败时只根据明确错误修正一次/u);
  assert.match(agent, /普通单次反馈的典型 Payload（没有 `rubricPatches`）/u);
  assert.match(agent, /"atoms": \[/u);
  assert.match(agent, /"rubricPatches": \[/u);
  assert.match(agent, /sourceRefs: \["new:<operationRef>"\]/u);
  assert.match(agent, /action=update\|merge.*targetIds/u);
  assert.match(agent, /MEMORY_RECALL_COMPLETED.*MEMORY_RECALL_FAILED/su);
  assert.match(agent, /MEMORY_CAPTURE_COMPLETED.*MEMORY_CAPTURE_FAILED/su);
});

test("semantic and recall templates expose only stable L2B rubrics", () => {
  const prompt = fs.readFileSync(path.join(root, "templates/memory-agent-system-prompt.md"), "utf8");
  const recall = fs.readFileSync(path.join(root, "templates/writing-recall-prompt.xml"), "utf8");
  assert.match(prompt, /L0 Writing Episode.*L1 Atom Memory.*L2B Memory Rubrics/su);
  assert.match(prompt, /普通单次反馈通常停在 L1/u);
  assert.match(prompt, /不要采用机械次数阈值/u);
  assert.match(prompt, /L2B 不保存 candidate/u);
  assert.doesNotMatch(prompt, /L2 Context Memory|L3 Rubrics Memory/u);
  assert.match(recall, /memory_rubrics/u);
  assert.match(recall, /<memory-rubrics>/u);
  assert.doesNotMatch(recall, /writing_context|specific_memories|<writing-context>/u);
});

test("plugin and maintenance use isolated V2-0821 identifiers", () => {
  const plugin = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
  assert.equal(plugin.name, "research-report-memory-v2-0821");
  assert.deepEqual(plugin.agents, ["./agents/research-report-memory-curator.md"]);
  assert.equal(
    plugin.mcpServers["research-report-memory-v2-0821"].env.RESEARCH_REPORT_MEMORY_V2_0821_DIR,
    "~/.research-report-memory-v2-0821",
  );
  const mcp = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  assert.equal(mcp.mcpServers["research-report-memory-v2-0821"].command, "sh");

  const script = fs.readFileSync(path.join(root, "scripts/run-memory-maintenance-workbuddy.sh"), "utf8");
  assert.match(script, /RESEARCH_REPORT_MEMORY_V2_0821_DIR/u);
  assert.match(script, /mcp__research-report-memory-v2-0821__writing_memory_recall/u);
  assert.match(script, /L0\/L1.*L2B/u);
  assert.doesNotMatch(script, /research-report-memory-v2-mvp|RESEARCH_REPORT_MEMORY_V2_DIR/u);
});
