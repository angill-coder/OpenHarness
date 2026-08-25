import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("integrated Skill uses the Python runner and versioned Rubric Set", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const loop = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/loop-orchestration.md"),
    "utf8",
  );
  const orchestration = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  assert.match(skill, /Python Runner/su);
  assert.match(skill, /userInputEvidence/u);
  assert.match(skill, /可被素材验证、反驳或修正的完整判断/u);
  assert.match(skill, /不能只写成主题、关键词或短标题/u);
  assert.match(skill, /不要为了适配多选框而压缩观点/u);
  assert.match(skill, /只启动一次 Python Runner/u);
  assert.match(skill, /finalArtifactPath/u);
  assert.match(loop, /"schemaVersion": 2/u);
  assert.match(loop, /Resolution Judge.*sourceL1/su);
  assert.match(loop, /all six dimension Judges/u);
  assert.match(loop, /run-python\.cmd.*runner\.py.*--job/su);
  assert.match(loop, /matching shell wrapper/u);
  assert.match(loop, /only execution entry/u);
  assert.match(skill, /operation=capture/u);
  assert.match(skill, /Memory MCP/u);
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
  assert.match(skill, /旧 Report Loop MCP 流程/u);
  assert.match(loop, /Report Loop has no MCP server/u);
});
