import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("integrated Skill uses the Python runner and versioned Rubric Set", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const orchestration = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  assert.match(skill, /Python Runner/su);
  assert.match(skill, /Base .* core .* audience .* project/su);
  assert.match(skill, /writing_memory_recall/u);
  assert.match(skill, /operation=capture/u);
  assert.match(skill, /Memory MCP/u);
  assert.match(skill, /skills\/\*\*/u);
  assert.match(skill, /rubrics\/\*\*/u);
  assert.match(orchestration, /Memory Recall/u);
  assert.match(orchestration, /operation=capture/u);
  assert.match(orchestration, /~\/\.workbuddy\/MEMORY\.md/u);
});

test("integrated manifests register only Memory MCP and one Curator", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const plugin = JSON.parse(
    fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
  );
  const mcp = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  assert.equal(plugin.name, "research-report-loop-memory");
  assert.equal(plugin.mcpServers, "./.mcp.json");
  assert.deepEqual(Object.keys(mcp.mcpServers), ["research-report-memory-v2-0821"]);
  assert.deepEqual(plugin.agents, ["./agents/research-report-memory-curator.md"]);
  assert.equal(plugin.hooks, "./hooks/hooks.json");
  assert.match(skill, /research-report-memory-v2-0821/u);
  assert.match(skill, /Report Loop .* MCP Server/su);
});
