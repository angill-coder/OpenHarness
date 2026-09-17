import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("integrated Skill uses the host launcher and versioned Rubric Set", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const loop = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/loop-orchestration.md"),
    "utf8",
  );
  const orchestration = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  const architecture = fs.readFileSync(
    path.join(root, "docs/report-loop-architecture.md"),
    "utf8",
  );
  assert.match(skill, /Python Runner/su);
  assert.match(skill, /userInputEvidence/u);
  assert.match(skill, /确认这份汇报给谁看、什么场合/u);
  assert.doesNotMatch(skill, /汇报背景.*支撑什么决策/u);
  assert.match(skill, /由 Agent 基于素材主动提出以下三项建议/u);
  assert.match(skill, /先在普通回复中展示细节/u);
  assert.match(skill, /再用 `AskUserQuestion` 做简短确认/u);
  assert.match(skill, /允许用户补充或修改/u);
  assert.match(skill, /提出几个可能的汇报背景选项供用户确认/u);
  assert.match(skill, /可被验证、反驳或修正的完整判断/u);
  assert.match(skill, /不能只写主题、关键词或短标题/u);
  assert.match(skill, /先在普通回复中展示论据清单/u);
  assert.match(skill, /AskUserQuestion/u);
  assert.match(skill, /finalArtifactPath/u);
  // 交付位置与版本号由 Runner 按报告目录约定推导，不再另建 <name>-versions/
  assert.match(skill, /报告目录\/\.report-agent/u);
  assert.match(skill, /<报告主题>-vN\.md/u);
  assert.doesNotMatch(skill, /versionsDirectory|\.report-loop\//u);
  assert.match(skill, /不得提前结束任务/u);
  assert.match(skill, /不得把 Job、状态或结果 JSON 当作交付物/u);
  assert.match(skill, /本轮初稿保存完成之前，不读取 Report Loop 执行卡/u);
  assert.match(skill, /不得事前阅读源码、运行测试、执行 `--help` 或预检/u);
  assert.match(loop, /"schemaVersion": 2/u);
  assert.match(loop, /# Report Loop 执行卡/u);
  assert.match(loop, /PostToolUse Hook/u);
  assert.match(loop, /TaskOutput/u);
  assert.match(loop, /run_in_background=true/u);
  assert.match(loop, /<task-notification>/u);
  assert.match(loop, /结果文件绝对路径/u);
  assert.match(loop, /宿主侧 Launcher/u);
  assert.match(loop, /插件 Hook 会自动在宿主侧启动 Runner/u);
  assert.match(loop, /内部控制结果，不直接展示给用户/u);
  assert.match(loop, /Job 文件名不是触发条件/u);
  assert.match(loop, /每轮 Report Loop 只创建并写入一个 Job 文件/u);
  assert.doesNotMatch(loop, /<PLUGIN_ROOT>|run-python\.(?:sh|cmd)/u);
  assert.match(loop, /不要事前阅读 Runner 源码、运行测试、执行 `--help` 或预检/u);
  assert.doesNotMatch(loop, /sourceL1|rubric_compiler|judge_batch|Persistent Rewriter/u);
  assert.match(architecture, /Resolution Judge.*sourceL1/su);
  assert.match(architecture, /judge_batch\.py/u);
  assert.match(architecture, /Persistent Rewriter/u);
  assert.match(skill, /operation=capture/u);
  assert.match(skill, /默认启用/u);
  assert.match(skill, /关闭时只使用 Base Rubrics/u);
  assert.match(skill, /不得因用户反馈修改 Skill、Base Rubrics、插件代码/u);
  assert.doesNotMatch(skill, /report_loop_(?:start|submit|finish|status)/u);
  assert.match(orchestration, /operation=resolve/u);
  assert.match(orchestration, /Resolution Judge/u);
  assert.match(orchestration, /operation=capture/u);
  assert.match(orchestration, /~\/\.workbuddy\/MEMORY\.md/u);
});

test("integrated manifests declare the team agents and no MCP", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const plugin = JSON.parse(
    fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
  );
  const orchestrationForManifest = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  assert.equal(plugin.name, "report-agent-v3");
  // Loop 由 Hook 直接启动；插件不声明任何 MCP
  assert.equal(fs.existsSync(path.join(root, ".mcp.json")), false);
  assert.equal(plugin.dependencies, undefined);
  // 记忆不经过 MCP：存放在用户主目录下可见的 ReportAgentMemory/
  assert.match(orchestrationForManifest, /ReportAgentMemory\//u);
  assert.deepEqual(plugin.agents, [
    "./agents/report-team-lead.md",
    "./agents/report-evidence-agent.md",
    "./agents/report-writer.md",
    "./agents/report-reviewer.md",
    "./agents/report-memory-agent.md",
  ]);
  assert.equal(plugin.hooks, "./hooks/hooks.json");
  const orchestration = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  const loop = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/loop-orchestration.md"),
    "utf8",
  );
  assert.match(orchestration, /report-memory-agent/u);
  assert.match(loop, /Hook 只是宿主侧 Launcher/u);
  assert.match(loop, /不要执行 ToolSearch/u);
  assert.match(loop, /没有可调用的 MCP 工具/u);
});

test("Writing Instruction and Base Rubric require hierarchical body organization", () => {
  const writing = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/writing-instructions.md"),
    "utf8",
  );
  const rubric = JSON.parse(
    fs.readFileSync(path.join(root, "rubrics/v2_rubric_research.json"), "utf8"),
  );
  const expression = rubric.dimensions.find((dimension: { name?: string }) => dimension.name === "expression");
  const check = expression?.checks?.find((item: { id?: string }) => item.id === "E7");

  assert.equal(rubric.version, "v2.4");
  assert.match(writing, /中心判断或短过渡.*一级 bullet.*二级 bullet/su);
  assert.match(writing, /连续长段堆叠.*扁平 bullet/su);
  assert.equal(check?.label, "正文层级化组织");
  assert.match(check?.desc ?? "", /中心判断或短过渡.*一级 bullet.*二级 bullet/su);
  assert.match(check?.effect ?? "", /连续长段.*层级混乱.*扁平 bullet/su);
});

test("developer docs stay factually aligned with the code they describe", () => {
  // docs/ 不随包分发，Agent 读不到；但排查故障的人会照着它找文件。
  // 过时的路径比缺失的文档更糟——会把人引向不存在的模块。
  const architecture = fs.readFileSync(path.join(root, "docs/report-loop-architecture.md"), "utf8");

  // 文档里提到的每个仓库内路径都必须真实存在
  const referenced = [...architecture.matchAll(/`((?:hooks|report_loop|core|scripts|rubrics|skills|resources)\/[^`]+?)`/gu)]
    .map((match) => match[1])
    .map((value) => (value.startsWith("core/") ? `report_loop/${value}` : value))
    .filter((value) => !value.includes("*") && /\.(?:ts|py|mjs|json|md)$/u.test(value));
  assert.ok(referenced.length >= 8, `expected several module references, got ${referenced.length}`);
  for (const relative of referenced) {
    assert.equal(fs.existsSync(path.join(root, relative)), true, `docs reference missing: ${relative}`);
  }

  // 已删除的架构不得复活到文档里
  assert.doesNotMatch(architecture, /mcp\/src\//u);
  assert.doesNotMatch(architecture, /MCP 兼容入口|versionsDirectory|版本记录目录/u);

  // Memory Rubric 示例里的 L1 ID 必须符合实际校验规则
  const evolution = fs.readFileSync(path.join(root, "docs/rubric-set-evolution.md"), "utf8");
  const store = fs.readFileSync(path.join(root, "report_loop/core/memory_store.py"), "utf8");
  const atomPattern = store.match(/_ATOM_ID = re\.compile\(r"\^(L1-)/u);
  assert.ok(atomPattern, "memory_store.py should pin the L1 id prefix");
  for (const sample of evolution.matchAll(/"sourceL1Ids": \[([^\]]+)\]/gu)) {
    for (const id of sample[1].split(",").map((value) => value.trim().replace(/"/gu, ""))) {
      assert.match(id, /^L1-/u, `doc sample uses an invalid L1 id: ${id}`);
    }
  }
});
