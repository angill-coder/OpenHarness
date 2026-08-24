import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";


const root = path.resolve(import.meta.dirname, "../..");

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

test("integrated manifests register two MCP servers and one Curator", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const plugin = JSON.parse(
    fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
  );
  assert.equal(plugin.name, "research-report-loop-memory");
  assert.deepEqual(
    Object.keys(plugin.mcpServers).sort(),
    ["research-report-loop", "research-report-memory-v2-0821"],
  );
  assert.deepEqual(plugin.agents, ["./agents/research-report-memory-curator.md"]);
  assert.equal(plugin.hooks, "./hooks/hooks.json");
  assert.match(skill, /独立的 `research-report-memory-v2-0821`/u);
  assert.match(skill, /不需要用户再说“请记住”/u);
});
