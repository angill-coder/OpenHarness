import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const current = fs.readFileSync(path.join(root, "agents/research-report-memory-curator.md"), "utf8");
const letta = fs.readFileSync(path.join(root, "prompts/research-report-memory-curator-v2-letta-first.md"), "utf8");
const reflection = fs.readFileSync(path.join(root, "agents/research-report-memory-reflection.md"), "utf8");

test("both Curator prompts keep the same Memory MCP and independent rubric contract", () => {
  for (const prompt of [current, letta]) for (const marker of [
    "name: research-report-memory-curator",
    "writing_memory_recall",
    "writing_memory_capture_payload",
    "L0 Writing Episode",
    "L1 Atom Memory",
    "L2B Memory Rubrics",
    "core / audience / project",
    "statement",
    "sourceRefs",
    "sourceL1Ids",
    "Base Rubrics",
    "agentContext",
    "agentContextDocument",
    '"snapshotRevision"',
    '"episode"',
    '"conversationExcerpt"',
    'sourceRefs:["new:<operationRef>"]',
    "第二次仍失败",
  ]) assert.ok(prompt.includes(marker), `prompt is missing ${marker}`);
});

test("both Curator prompts forbid guessed Capture fields and distinguish updated from unchanged L1 sources", () => {
  for (const prompt of [current, letta]) {
    assert.match(prompt, /不使用 `episodes` 或 `feedbackReviewSnapshot`/u);
    assert.match(prompt, /更新或合并既有 L1.*旧 `sourceL1Ids`/u);
  }
});

test("Memory Agent is principle-based and does not pre-resolve Base", () => {
  for (const prompt of [current, letta]) {
    assert.match(prompt, /只维护独立 Memory Rubrics/u);
    assert.match(prompt, /Resolution Judge/u);
    assert.doesNotMatch(prompt, /dimension=personal|operation=extend|Base → core → audience → project/u);
  }
});

test("Letta-first variant retains conservative experiential judgment", () => {
  for (const marker of ["未来写作和评测变得更好", "作为一个整体理解", "最小且有长期价值的更新", "保持 L2B 不变", "不要使用固定命中次数、打分阈值", "不是要求每轮逐层晋升的流程"])
    assert.ok(letta.includes(marker), `Letta-first prompt is missing ${marker}`);
  assert.doesNotMatch(letta, /普通单次反馈通常停在 L1|明确强烈的长期要求可更快进入 L2B/u);
});

test("Letta-first variant retains current Scope and Capture safety contracts", () => {
  for (const marker of ["当前任务的受众和项目不能反推 Scope", "范围不明时停在 L0", "L1 `action=update|merge` 使用复数 `targetIds`", "Capture 期间不得单独 Forget", "operation=manage"])
    assert.ok(letta.includes(marker), `Letta-first prompt is missing current contract ${marker}`);
});

test("Reflection consolidates the same layers and only commits increments", () => {
  for (const marker of ["Investigate", "Reflect", "Update", "Commit", '"mode": "reflection"', "reflectionThrough", "只维护独立 Memory Rubrics", "agentContext", "agentContextDocument"])
    assert.ok(reflection.includes(marker), `Reflection prompt is missing ${marker}`);
  assert.doesNotMatch(reflection, /dimension=personal|operation=extend/u);
});
