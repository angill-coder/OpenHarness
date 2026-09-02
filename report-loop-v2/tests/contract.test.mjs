import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function read(relative) {
  return fs.readFileSync(path.join(root, relative), "utf8");
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

test("V2 is an isolated native Expert without V1 runtime components", () => {
  const manifest = JSON.parse(read(".codebuddy-plugin/plugin.json"));
  const serialized = JSON.stringify(manifest);

  assert.equal(manifest.name, "report-loop-v2");
  assert.equal(manifest.agentName, "report-loop-v2");
  assert.equal(manifest.expertType, "agent");
  assert.deepEqual(manifest.skills, ["./skills/research-report-loop-v2"]);
  assert.equal(manifest.agents.length, 5);
  assert.doesNotMatch(serialized, /mcpServers|hooks|commands/u);

  for (const forbidden of ["hooks", "mcp", "dist", "bin", "node_modules"]) {
    assert.equal(fs.existsSync(path.join(root, forbidden)), false, forbidden);
  }
});

test("main Agent writes V0 and dynamic dimensions are explicit", () => {
  const skill = read("skills/research-report-loop-v2/SKILL.md");
  const expert = read("agents/report-loop-v2.md");
  const resolution = read("agents/report-resolution-judge-v2.md");

  assert.match(skill, /第 0 步：盘点并解析素材/u);
  assert.match(skill, /摘要观点假设（hypothesis）/u);
  assert.match(skill, /按规则写出初稿 V0/u);
  assert.match(skill, /loop-orchestration\.md/u);
  assert.match(skill, /memory-orchestration\.md/u);
  assert.match(expert, /不要把 V0 委派给 Rewriter/u);
  assert.match(resolution, /维度数量不固定/u);
  assert.match(resolution, /dimensionCandidate/u);
});

test("native orchestration details stay in references rather than crowding the main Skill", () => {
  const skill = read("skills/research-report-loop-v2/SKILL.md");
  const loop = read("skills/research-report-loop-v2/references/loop-orchestration.md");
  const state = read("skills/research-report-loop-v2/references/state-and-scoring.md");
  const memory = read("skills/research-report-loop-v2/references/memory-orchestration.md");

  assert.doesNotMatch(skill, /Σ\(score × weight\)/u);
  assert.match(loop, /有 N 个维度就调用 N 次/u);
  assert.match(loop, /并发上限为 6/u);
  assert.match(state, /overall = Σ\(dimensionScore × weight\)/u);
  assert.match(loop, /三项确认各自对应的一段用户消息原文/u);
  assert.match(loop, /单个文件，也可以指向整个素材目录/u);
  assert.match(state, /下降不超过 `0\.15`/u);
  assert.match(loop, /运行约一小时/u);
  assert.doesNotMatch(loop, /三轮 Rewrite/u);
  assert.match(memory, /直接修改当前报告/u);
  assert.match(memory, /普通报告写作反馈一律走 Capture/u);
  assert.match(memory, /不得改走 Manage/u);
  assert.match(memory, /明确要求忘记/u);
  assert.match(memory, /MEMORY_CAPTURE_COMPLETED/u);
});

test("only Memory Agent has persistent user memory", () => {
  const agents = fs.readdirSync(path.join(root, "agents")).filter((name) => name.endsWith(".md"));
  for (const agent of agents) {
    const content = read(`agents/${agent}`);
    if (agent === "report-memory-agent-v2.md") {
      assert.match(content, /^memory: user$/mu);
    } else {
      assert.doesNotMatch(content, /^memory:/mu);
    }
    assert.doesNotMatch(content, /mcp__/u);
  }
});

test("Base Rubrics are present and keep the six stable base dimensions", () => {
  const source = read("rubrics/base-rubrics.json");
  const rubric = JSON.parse(source);
  assert.equal(sha256(source), "41c84485be4c7991ea6068494e208eddffa3163755c91472d968e8d7a4f4829e");
  assert.deepEqual(rubric.dimensions.map((item) => item.name), [
    "traceability",
    "structure",
    "narrative",
    "insight",
    "coverage",
    "expression",
  ]);
  assert.equal(rubric.aggregate, "weighted_avg");
});

test("V2 preserves the frozen V1 writing instructions", () => {
  const instructions = read("skills/research-report-loop-v2/references/writing-instructions.md");
  assert.equal(sha256(instructions), "34d08ecd42b934716afcda29807aafe289f6ac1410bc904137e5d9dfe92b4269");
});

test("V2 preserves V1 deterministic scoring, adoption, stop, and recovery semantics", () => {
  const loop = read("skills/research-report-loop-v2/references/loop-orchestration.md");
  const state = read("skills/research-report-loop-v2/references/state-and-scoring.md");
  const judge = read("agents/report-dimension-judge-v2.md");

  assert.match(state, /met = 1\.0[\s\S]*partial = 0\.5[\s\S]*miss = 0\.0/u);
  assert.match(state, /dimensionScore = 1 \+ 4 × average\(checkValues\)/u);
  assert.match(state, /维度分最高为 `2\.0`/u);
  assert.match(state, /overall = Σ\(dimensionScore × weight\)/u);
  assert.match(state, /overall 存在且不低于历史最佳/u);
  assert.match(state, /`noImprovementStreak >= 2`/u);
  assert.match(state, /`user_cancelled`/u);
  assert.match(state, /`stateRevision`/u);
  assert.match(loop, /最多重试 3 次/u);
  assert.doesNotMatch(judge, /^\s*"score"\s*:/mu);
  assert.match(judge, /^model: gpt-5\.6-sol$/mu);
});

test("Resolution supports one on-demand L1 inspection and validates every Memory candidate", () => {
  const loop = read("skills/research-report-loop-v2/references/loop-orchestration.md");
  const resolution = read("agents/report-resolution-judge-v2.md");
  const memory = read("agents/report-memory-agent-v2.md");

  assert.match(resolution, /status":"needs_source"/u);
  assert.match(resolution, /inspectSourceFor/u);
  assert.match(resolution, /每条 Memory Rubric 必须且只能作出一次决定/u);
  assert.match(resolution, /Base Dimension、criteria、anchors、Check/u);
  assert.match(resolution, /`interpret` 只能/u);
  assert.match(resolution, /^model: gpt-5\.6-sol$/mu);
  assert.match(loop, /operation=inspect_sources/u);
  assert.match(loop, /不得允许第二次溯源请求/u);
  assert.match(memory, /### `operation=inspect_sources`/u);
  assert.match(memory, /MEMORY_SOURCE_CONFLICT/u);
});

test("Memory capture is idempotent and Reflection is due once per day", () => {
  const memory = read("agents/report-memory-agent-v2.md");
  const orchestration = read("skills/research-report-loop-v2/references/memory-orchestration.md");

  assert.match(memory, /稳定且重试时复用的 `captureId`/u);
  assert.match(memory, /idempotent=true/u);
  assert.match(memory, /Episode → Atom\/L2B 与 history → `MEMORY\.md`/u);
  assert.match(memory, /当地时间已过 16:30/u);
  assert.match(orchestration, /重试时必须复用/u);
});

test("Rewriter always starts from the accepted best version and receives a compact brief", () => {
  const loop = read("skills/research-report-loop-v2/references/loop-orchestration.md");
  const state = read("skills/research-report-loop-v2/references/state-and-scoring.md");
  const rewriter = read("agents/report-rewriter-v2.md");

  assert.match(loop, /只能从历史最佳版本生成新候选/u);
  assert.match(state, /`repair`/u);
  assert.match(state, /`preserve`/u);
  assert.match(state, /`avoid`/u);
  assert.match(rewriter, /revisionBrief/u);
  assert.match(rewriter, /不读取或推断未传入的原始 Judge 对话/u);
});

test("build emits a self-contained Expert without platform launchers", async () => {
  await import(`../scripts/build.mjs?test=${Date.now()}`);
  const target = path.join(root, "release/report-loop-v2-expert");
  const manifest = JSON.parse(fs.readFileSync(path.join(target, ".codebuddy-plugin/plugin.json"), "utf8"));

  assert.equal(manifest.name, "report-loop-v2");
  assert.equal(manifest.version, "0.2.0");
  assert.equal(fs.existsSync(path.join(target, "rubrics/base-rubrics.json")), true);
  assert.equal(fs.existsSync(path.join(target, "skills/research-report-loop-v2/SKILL.md")), true);
  assert.equal(fs.existsSync(path.join(target, "skills/research-report-loop-v2/references/state-and-scoring.md")), true);
  assert.equal(fs.existsSync(path.join(target, "scripts")), false);
  assert.equal(fs.existsSync(path.join(target, "hooks")), false);
});
