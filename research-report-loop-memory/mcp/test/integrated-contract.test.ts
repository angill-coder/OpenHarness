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
  assert.match(skill, /这份汇报给谁看、什么场合？/u);
  assert.doesNotMatch(skill, /汇报背景.*支撑什么决策/u);
  assert.match(skill, /可被素材验证、反驳或修正的完整判断/u);
  assert.match(skill, /不能只写成主题、关键词或短标题/u);
  assert.match(skill, /不要为了适配多选框而压缩观点/u);
  assert.match(skill, /插件 Hook 会在宿主侧自动启动 Runner/u);
  assert.match(skill, /AskUserQuestion/u);
  assert.match(skill, /TaskOutput/u);
  assert.match(skill, /Bash\(run_in_background=true\)/u);
  assert.match(skill, /<task-notification>/u);
  assert.match(skill, /finalArtifactPath/u);
  assert.match(skill, /V1 保存完成之前，不读取 Report Loop 执行卡/u);
  assert.match(skill, /已经验证的 Python Runner/u);
  assert.match(skill, /不得事前阅读源码、运行测试、执行 `--help` 或预检/u);
  assert.match(loop, /"schemaVersion": 2/u);
  assert.match(loop, /# Report Loop 执行卡/u);
  assert.match(loop, /PostToolUse Hook/u);
  assert.match(loop, /TaskOutput/u);
  assert.match(loop, /run_in_background=true/u);
  assert.match(loop, /<task-notification>/u);
  assert.match(loop, /结果文件绝对路径/u);
  assert.match(loop, /宿主侧 Launcher/u);
  assert.match(loop, /Job 文件名不是触发条件/u);
  assert.match(loop, /每轮 Report Loop 只创建并写入一个 Job 文件/u);
  assert.doesNotMatch(loop, /<PLUGIN_ROOT>|run-python\.(?:sh|cmd)/u);
  assert.match(loop, /不要事前阅读 Runner 源码、运行测试、执行 `--help` 或预检/u);
  assert.doesNotMatch(loop, /sourceL1|rubric_compiler|judge_batch|Persistent Rewriter/u);
  assert.match(architecture, /Resolution Judge.*sourceL1/su);
  assert.match(architecture, /judge_batch\.py/u);
  assert.match(architecture, /Persistent Rewriter/u);
  assert.match(skill, /operation=capture/u);
  assert.match(skill, /Memory MCP/u);
  assert.match(skill, /默认关闭/u);
  assert.match(skill, /writing_memory_settings\(action=enable\)/u);
  assert.match(skill, /关闭时只使用 Base Rubrics/u);
  assert.match(skill, /不得因用户反馈修改 Skill、Base Rubrics、插件代码/u);
  assert.doesNotMatch(skill, /report_loop_(?:start|submit|finish|status)/u);
  assert.match(orchestration, /Memory Recall/u);
  assert.match(orchestration, /Resolution Judge/u);
  assert.match(orchestration, /operation=capture/u);
  assert.match(orchestration, /~\/\.workbuddy\/MEMORY\.md/u);
});

test("integrated manifests register only Memory MCP plus Curator and Reflection", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const plugin = JSON.parse(
    fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
  );
  const mcp = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  assert.equal(plugin.name, "research-report-loop-memory");
  assert.equal(plugin.mcpServers, "./.mcp.json");
  assert.deepEqual(Object.keys(mcp.mcpServers), ["report-memory-v2"]);
  assert.deepEqual(plugin.agents, [
    "./agents/research-report-memory-curator.md",
    "./agents/research-report-memory-reflection.md",
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
  assert.match(orchestration, /report-memory-v2/u);
  assert.match(orchestration, /writing_memory_settings/u);
  assert.match(skill, /Hook 只负责在 Agent 沙箱外启动/u);
  assert.match(loop, /不要执行 ToolSearch/u);
  assert.match(loop, /不要调用 `report_loop_run`/u);
});
