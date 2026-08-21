import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("skill delegates recall and capture to the WB Memory Sub-agent", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report/SKILL.md"), "utf8");
  const orchestration = fs.readFileSync(path.join(root, "skills/research-report/references/memory-orchestration.md"), "utf8");
  const instructions = fs.readFileSync(path.join(root, "skills/research-report/references/instructions.md"), "utf8");
  const upstreamSkill = fs.readFileSync(path.join(root, "upstream/research-report/SKILL.md"), "utf8");
  const upstreamInstructions = fs.readFileSync(path.join(root, "upstream/research-report/instructions.md"), "utf8");
  assert.match(skill, /references\/memory-orchestration\.md/u);
  assert.doesNotMatch(upstreamSkill, /memory-orchestration/u);
  const skillWithoutOverlay = skill.replace(
    /\n\n## Memory 调度\n\n执行本 Skill 时，必须同时读取并遵守 `references\/memory-orchestration\.md`。该文件是独立的 Memory 调度契约，不属于写作内容；不得将其中的流程、工具或规则写入报告正文。\n/u,
    "\n",
  );
  assert.equal(skillWithoutOverlay, upstreamSkill);
  assert.match(orchestration, /三项需求澄清.*research-report-memory-curator.*recall.*MEMORY_RECALL_COMPLETED/su);
  assert.match(orchestration, /不得只输出.*准备召回.*后结束/su);
  assert.match(orchestration, /完成当前修改.*research-report-memory-curator.*再交付或总结/su);
  assert.match(orchestration, /普通报告反馈永远走 `capture`/u);
  assert.match(orchestration, /不得改走 `manage` 或先调用 `forget`/u);
  assert.match(orchestration, /只有用户明确要求查看、纠错、重新分类、合并或删除 Memory 本身时才使用 `manage`/u);
  assert.match(orchestration, /L0 原始对话窗口.*逐字复制/su);
  assert.match(orchestration, /通常 2–6 条、最多 8 条/u);
  assert.match(orchestration, /conversationExcerpt.*不能相互替代/u);
  assert.match(orchestration, /本轮用户明确要求.*project.*audience.*core.*本 Skill/su);
  assert.match(orchestration, /MEMORY_RECALL_FAILED.*MEMORY_CAPTURE_FAILED/su);
  assert.match(orchestration, /继续原写作任务/u);
  assert.doesNotMatch(orchestration, /writing_memory_recover/u);
  assert.equal(instructions, upstreamInstructions);
  assert.match(instructions, /主张—证据/u);
  assert.match(instructions, /任何硬规则未通过，均不得交付/u);
});

test("Memory Agent contract covers L0-L3 and four operations", () => {
  const agent = fs.readFileSync(path.join(root, "agents/research-report-memory-curator.md"), "utf8");
  assert.match(agent, /maxTurns: 24/u);
  assert.match(agent, /^tools:.*writing_memory_recall.*writing_memory_capture_payload.*writing_memory_forget/mu);
  assert.doesNotMatch(agent.match(/^tools:.*$/mu)?.[0] ?? "", /ToolSearch|DeferExecuteTool/u);
  assert.match(agent, /Capture 只调用 `writing_memory_capture_payload`/u);
  assert.match(agent, /JSON 字符串/u);
  assert.match(agent, /根字段固定为单数 `episode` 和复数 `memories`/u);
  assert.match(agent, /Runtime 会在有效 Capture 中创建 Episode，不需要预先创建/u);
  assert.match(agent, /Feedback Capture 标准 Payload/u);
  assert.match(agent, /L0 原始对话窗口/u);
  assert.match(agent, /通常保留 2–6 条，最多 8 条/u);
  assert.match(agent, /最后一条必须是当前用户反馈/u);
  assert.match(agent, /"conversationExcerpt": \[/u);
  assert.match(agent, /"episode": \{/u);
  assert.match(agent, /"memories": \[/u);
  assert.match(agent, /"rule": "一条独立、可复用的写作要求"/u);
  assert.match(agent, /`scopeValue` 保存具体名称/u);
  assert.match(agent, /MEMORY_CAPTURE_FAILED/u);
  assert.match(agent, /MEMORY_RECALL_FAILED/u);
  assert.match(agent, /目标不是保存更多内容/u);
  assert.match(agent, /是否会改变未来的报告写作或质量判断/u);
  assert.match(agent, /L2\/L3 是稀缺的写作上下文/u);
  assert.match(agent, /不修改 Skill、Hook 或宿主配置/u);
  assert.match(agent, /换项目、换受众仍成立的可复用写作规则归 `core`/u);
  assert.match(agent, /当前任务的 Audience\/Project.*Recall 路由/u);
  assert.match(agent, /Scope 适用于每个 L1\/L2\/L3 Item/u);
  assert.match(agent, /L1 首次确定 Scope，L2\/L3 继承相同 Scope/u);
  assert.match(agent, /每条进入长期记忆的写作要求/u);
  assert.doesNotMatch(agent, /对每个 L1 Atom 按以下流程/u);
  assert.match(agent, /多个独立项目/u);
  assert.match(agent, /“这份报告”“这一版”.*不能单独证明写入范围/u);
  assert.match(agent, /换项目、换受众仍成立的可复用写作规则归 `core`/u);
  assert.match(agent, /项目背景、口径、目标、关键结论、前因后果/u);
  assert.match(agent, /sourceRefs: \["new:<operationRef>"\]/u);
  assert.match(agent, /action=update\|merge.*targetIds: \["<原 L1 ID>"\]/u);
  assert.match(agent, /`id` 和单数 `targetId` 都不是有效替代字段/u);
  assert.match(agent, /"targetIds": \["m_existing_l1_id"\]/u);
  assert.match(agent, /`extractor` 由 Runtime 自动写入/u);
  assert.match(agent, /document_source_scope_mismatch/u);
  assert.match(agent, /`recall`、`capture`、`maintenance` 或 `manage`/u);
  assert.match(agent, /用户在评价报告、要求改写报告或提出写作要求时一律执行 `capture`/u);
  assert.match(agent, /Capture 期间禁止调用 `writing_memory_forget`/u);
  assert.match(agent, /update\/merge \+ targetIds.*原子替换/u);
  assert.match(agent, /若用户只是在评价或修改报告，应执行 `capture`，不得执行 `manage`/u);
  assert.match(agent, /L0 Writing Episode/u);
  assert.match(agent, /L3 Rubrics Memory/u);
  assert.match(agent, /分类总览：Layer × Scope/u);
  assert.match(agent, /每个 L1\/L2\/L3 Item 只能选择一个 Scope/u);
  assert.match(agent, /L2 指导以后怎样写，L3 判断以后是否写到位/u);
  assert.match(agent, /Layer 处理/u);
  assert.match(agent, /Scope 是三选一；Layer 不是四选一/u);
  assert.match(agent, /Gate 0[\s\S]*L0 Writing Episode[\s\S]*Gate 1[\s\S]*L1 Atom Memory[\s\S]*Gate 2[\s\S]*L2 Context Memory[\s\S]*Gate 3[\s\S]*L3 Rubrics Memory/u);
  assert.match(agent, /进入更高层不等于一定新增内容/u);
  assert.match(agent, /L2 和 L3 的门槛不同/u);
  assert.match(agent, /当前最准确、最精简的有效版本/u);
  assert.match(agent, /历史由 L0 与 Git 保留/u);
  assert.match(agent, /整合价值/u);
  assert.match(agent, /能写出清晰的 `criterion \/ pass \/ fail`/u);
  assert.match(agent, /L3 不要求本轮必须新增 L2/u);
  for (const gate of ["Gate 0", "Gate 1", "Gate 2", "Gate 3"]) {
    const start = agent.indexOf(`### ${gate}`);
    const next = agent.indexOf("### Gate", start + 1);
    const section = agent.slice(start, next === -1 ? agent.indexOf("### 两条总原则") : next);
    assert.match(section, /Good case（通过）/u, `${gate} should include a good case`);
    assert.match(section, /Bad case（拒绝）/u, `${gate} should include a bad case`);
  }
  assert.match(agent, /L0 保留来源语境/u);
  assert.match(agent, /L3 Rubrics Memory.*默认 Recall.*按需检索/su);
  assert.match(agent, /L1 Atom Memory.*默认不暴露在写作上下文/su);
  assert.match(agent, /精简的原子证据层/u);
  assert.doesNotMatch(agent, /补充约束/u);
  assert.match(agent, /Writing Core（`core`）<br>常驻上下文、跨项目生效的写作要求/u);
  assert.match(agent, /Audience Memory（`audience`）<br>针对特定受众和汇报环境的写作要求/u);
  assert.match(agent, /Project Memory（`project`）<br>针对特定项目的写作要求与背景/u);
  assert.match(agent, /Scope 只使用/u);
  assert.match(agent, /purpose=review/u);
  assert.match(agent, /ACTIVE_RUBRICS/u);
  assert.match(agent, /MEMORY_RECALL_COMPLETED/u);
  assert.match(agent, /MEMORY_CAPTURE_COMPLETED/u);
  const layerSection = agent.slice(agent.indexOf("## 2. Layer 处理"), agent.indexOf("## 3. 冲突与更新"));
  assert.doesNotMatch(layerSection, /^\| Layer \|/mu);
  assert.ok(agent.indexOf("## 1. Scope 判定") < agent.indexOf("## 2. Layer 处理"));
  assert.ok(agent.indexOf("## 2. Layer 处理") < agent.indexOf("## 5. MCP 调用契约"));
});

test("Memory Agent semantic template uses the same Layer x Scope model", () => {
  const template = fs.readFileSync(path.join(root, "templates/memory-agent-system-prompt.md"), "utf8");
  assert.match(template, /记忆使用 \*\*Layer × Scope\*\* 两个独立维度/u);
  assert.match(template, /目标不是保存更多内容/u);
  assert.match(template, /是否会改变未来的报告写作或质量判断/u);
  assert.match(template, /L2\/L3 是稀缺的写作上下文/u);
  assert.match(template, /当前最准确、最精简的有效版本/u);
  assert.match(template, /每个 L1\/L2\/L3 Item 只能选择一个 Scope/u);
  assert.match(template, /Scope 适用于每个 L1\/L2\/L3 Item/u);
  assert.match(template, /L1 首次确定 Scope，L2\/L3 继承相同 Scope/u);
  assert.match(template, /每条进入长期记忆的写作要求/u);
  assert.match(template, /<layer-rules>[\s\S]*Gate 0[\s\S]*L0 Writing Episode[\s\S]*Gate 1[\s\S]*L1 Atom Memory[\s\S]*Gate 2[\s\S]*L2 Context Memory[\s\S]*Gate 3[\s\S]*L3 Rubrics Memory[\s\S]*<\/layer-rules>/u);
  assert.match(template, /进入更高层不等于一定新增内容/u);
  assert.match(template, /L2 和 L3 的门槛不同/u);
  assert.match(template, /L3 不要求本轮必须新增 L2/u);
  for (const gate of ["Gate 0", "Gate 1", "Gate 2", "Gate 3"]) {
    const start = template.indexOf(`## ${gate}`);
    const next = template.indexOf("## Gate", start + 1);
    const section = template.slice(start, next === -1 ? template.indexOf("## 总原则") : next);
    assert.match(section, /Good case（通过）/u, `${gate} template should include a good case`);
    assert.match(section, /Bad case（拒绝）/u, `${gate} template should include a bad case`);
  }
  assert.match(template, /Writing Core（`core`）.*Audience Memory（`audience`）.*Project Memory（`project`）/u);
  assert.match(template, /Writing Core（`core`）<br>常驻上下文、跨项目生效的写作要求/u);
  assert.match(template, /Audience Memory（`audience`）<br>针对特定受众和汇报环境的写作要求/u);
  assert.match(template, /Project Memory（`project`）<br>针对特定项目的写作要求与背景/u);
  assert.match(template, /L0 保留来源语境/u);
  assert.match(template, /精简的原子证据层/u);
  assert.match(template, /通常 2–6 条、最多 8 条/u);
  assert.match(template, /逐字保留，不用摘要替代/u);
  assert.doesNotMatch(template, /补充约束/u);
  assert.ok(template.indexOf("<scope-rules>") < template.indexOf("<layer-rules>"));
  assert.ok(template.indexOf("<layer-rules>") < template.indexOf("<runtime-contract>"));
});

test("WorkBuddy plugin registers hooks, agent and isolated MCP storage", () => {
  const hooks = JSON.parse(fs.readFileSync(path.join(root, "hooks/hooks.json"), "utf8"));
  for (const name of ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]) assert.ok(hooks.hooks[name]);
  assert.equal(hooks.hooks.SessionEnd, undefined);
  const plugin = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
  assert.equal(plugin.name, "research-report-memory-v2-mvp");
  assert.deepEqual(plugin.agents, ["./agents/research-report-memory-curator.md"]);
  assert.equal(plugin.mcpServers["research-report-memory-v2-mvp"].env.RESEARCH_REPORT_MEMORY_V2_DIR, "~/.research-report-memory-v2-mvp");
  const mcp = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  assert.equal(mcp.mcpServers["research-report-memory-v2-mvp"].command, "sh");
});

test("maintenance uses an isolated WB CLI memory session", () => {
  const script = fs.readFileSync(path.join(root, "scripts/run-memory-maintenance-workbuddy.sh"), "utf8");
  assert.match(script, /codebuddy|CODEBUDDY_BIN/u);
  assert.match(script, /--append-system-prompt/u);
  assert.match(script, /--plugin-dir "\$PLUGIN_ROOT"/u);
  assert.match(script, /MCP_CONFIG_FILE="\$DATA_DIR\/maintenance\/v2-mcp-config\.json"/u);
  assert.match(script, /dist\/server\.mjs/u);
  assert.match(script, /--mcp-config "\$MCP_CONFIG_FILE"/u);
  assert.match(script, /--strict-mcp-config/u);
  assert.match(script, /RESEARCH_REPORT_MEMORY_V2_DIR/u);
  assert.match(script, /mcp__research-report-memory-v2-mvp__writing_memory_recall/u);
  assert.match(script, /不得调用无 v2-mvp 后缀的旧版/u);
  assert.doesNotMatch(script, /\n\s*RESEARCH_REPORT_MEMORY_DIR="\$DATA_DIR"/u);
  assert.match(script, /L2.*L3/su);
  const template = fs.readFileSync(path.join(root, "scripts/maintenance-launchagent.plist.template"), "utf8");
  assert.match(template, /<key>Hour<\/key><integer>16<\/integer>/u);
  assert.match(template, /<key>Minute<\/key><integer>30<\/integer>/u);
});
