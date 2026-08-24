import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const currentPath = path.join(root, "agents/research-report-memory-curator.md");
const lettaPath = path.join(root, "prompts/research-report-memory-curator-v2-letta.md");
const current = fs.readFileSync(currentPath, "utf8");
const letta = fs.readFileSync(lettaPath, "utf8");

const sharedContract = [
  "name: research-report-memory-curator",
  "mcp__research-report-memory-v2-0821__writing_memory_recall",
  "mcp__research-report-memory-v2-0821__writing_memory_capture_payload",
  "mcp__research-report-memory-v2-0821__writing_memory_forget",
  "traceability / structure / narrative / insight / coverage / expression",
  "snapshotRevision",
  "externalSourceId",
  "conversationExcerpt",
  "sourceRefs",
  "sourceL1Ids",
  "MEMORY_RECALL_COMPLETED",
  "MEMORY_CAPTURE_COMPLETED",
];

test("both curator prompts keep the same domain and MCP contract", () => {
  for (const marker of sharedContract) {
    assert.ok(current.includes(marker), `current prompt is missing ${marker}`);
    assert.ok(letta.includes(marker), `Letta-first prompt is missing ${marker}`);
  }
});

test("Letta-first variant makes conservative experiential judgment the primary frame", () => {
  for (const marker of [
    "未来行为变得更好",
    "综合全部相关经验",
    "保持高层记忆精简",
    "渐进更新",
    "不新增或不更新 L2B",
    "不要使用固定命中次数或打分阈值",
  ]) assert.ok(letta.includes(marker), `Letta-first prompt is missing ${marker}`);
});

test("the active prompt remains the current V1 control", () => {
  assert.match(current, /## 3\. Layer Filter/u);
  assert.doesNotMatch(current, /Research Report Memory Curator — Letta-first Variant/u);
});
