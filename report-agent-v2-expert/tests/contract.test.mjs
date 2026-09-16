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

  assert.equal(manifest.name, "report-agent-v2");
  assert.equal(manifest.agentName, "report-agent-v2");
  assert.equal(manifest.expertType, "agent");
  assert.deepEqual(manifest.skills, ["./skills/research-report-agent-v2"]);
  assert.equal(manifest.agents.length, 6);
  assert.doesNotMatch(serialized, /mcpServers|hooks|commands/u);

  for (const forbidden of ["hooks", "mcp", "dist", "bin", "node_modules"]) {
    assert.equal(fs.existsSync(path.join(root, forbidden)), false, forbidden);
  }
});

test("main Agent delegates R0 to Writer and dynamic dimensions are explicit", () => {
  const skill = read("skills/research-report-agent-v2/SKILL.md");
  const expert = read("agents/report-agent-v2.md");
  const resolution = read("agents/report-resolution-judge-v2.md");

  assert.match(skill, /第 0 步：盘点并解析素材/u);
  assert.match(skill, /摘要观点假设（hypothesis）/u);
  assert.match(skill, /按规则写出初稿 R0/u);
  assert.match(skill, /loop-orchestration\.md/u);
  assert.match(skill, /memory-orchestration\.md/u);
  assert.match(expert, /委派 `report-writer-v2`/u);
  assert.match(skill, /writer-orchestration\.md/u);
  assert.match(resolution, /维度数量不固定/u);
  assert.match(resolution, /dimensionCandidate/u);
});

test("native orchestration details stay in references rather than crowding the main Skill", () => {
  const skill = read("skills/research-report-agent-v2/SKILL.md");
  const loop = read("skills/research-report-agent-v2/references/loop-orchestration.md");
  const state = read("skills/research-report-agent-v2/references/state-and-scoring.md");
  const memory = read("skills/research-report-agent-v2/references/memory-orchestration.md");

  assert.doesNotMatch(skill, /Σ\(score × weight\)/u);
  assert.match(loop, /每轮评测全部 N 个维度/u);
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

test("Capture delegation preserves user evidence without pre-classifying preferences", () => {
  const orchestration = read("skills/research-report-agent-v2/references/memory-orchestration.md");
  const main = read("agents/report-agent-v2.md");
  assert.match(orchestration, /原问题、选项及实际选择，区分 Agent 建议与用户表达/u);
  assert.match(orchestration, /不预先概括长期偏好或指定 Layer、Scope、Rubric/u);
  assert.match(orchestration, /上下文、报告内容和修改差异仅用于理解用户表达，不能代替用户表达/u);
  assert.match(orchestration, /没有明确的用户写作反馈时，不调用 Capture/u);
  assert.match(main, /写作反馈原文及必要语境/u);
  assert.doesNotMatch(main, /只把写作偏好交给/u);
});

test("Memory is explicitly file-backed and independent of host injection", () => {
  const agents = fs.readdirSync(path.join(root, "agents")).filter((name) => name.endsWith(".md"));
  for (const agent of agents) {
    const content = read(`agents/${agent}`);
    assert.doesNotMatch(content, /^memory:/mu);
    assert.doesNotMatch(content, /mcp__/u);
  }
  const memory = read("agents/report-memory-agent-v2.md");
  for (const name of ["memoryRoot", "ReportAgentMemory/", "MEMORY.md", "L0-episodes/", "L1-atoms/"]) {
    assert.ok(memory.includes(name), `Missing storage contract: ${name}`);
  }
  assert.doesNotMatch(memory, /`(?:episodes|atoms)\//u);
  assert.doesNotMatch(memory, /`history\/`|保留 history/u);
  assert.match(memory, /顶部明确写 `revision: N` 作为唯一版本号/u);
  assert.match(memory, /不新建 history 或 MEMORY 历史副本/u);
  const orchestration = read("skills/research-report-agent-v2/references/memory-orchestration.md");
  assert.match(orchestration, /USERPROFILE/u);
  assert.match(orchestration, /HOME/u);
  assert.match(orchestration, /resolve、inspect_sources、capture、manage、reflect 和 settings/u);
  assert.match(read("skills/research-report-agent-v2/references/loop-orchestration.md"), /memory-orchestration\.md#记忆位置/u);
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
  const instructions = read("skills/research-report-agent-v2/references/writing-instructions.md");
  assert.equal(sha256(instructions), "34d08ecd42b934716afcda29807aafe289f6ac1410bc904137e5d9dfe92b4269");
});

test("V2 preserves V1 deterministic scoring, adoption, stop, and recovery semantics", () => {
  const loop = read("skills/research-report-agent-v2/references/loop-orchestration.md");
  const state = read("skills/research-report-agent-v2/references/state-and-scoring.md");
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
  const loop = read("skills/research-report-agent-v2/references/loop-orchestration.md");
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
  const orchestration = read("skills/research-report-agent-v2/references/memory-orchestration.md");

  assert.match(memory, /稳定且重试时复用的 `captureId`/u);
  assert.match(memory, /idempotent=true/u);
  assert.match(memory, /Episode → Atom → `MEMORY\.md`/u);
  assert.match(memory, /当地时间已过 16:30/u);
  assert.match(orchestration, /重试时必须复用/u);
});

test("Writer always starts from the accepted best version and receives a compact brief", () => {
  const loop = read("skills/research-report-agent-v2/references/loop-orchestration.md");
  const state = read("skills/research-report-agent-v2/references/state-and-scoring.md");
  const writer = read("agents/report-writer-v2.md");

  assert.match(loop, /只能从历史最佳版本生成新候选/u);
  assert.match(state, /`repair`/u);
  assert.match(state, /`preserve`/u);
  assert.match(state, /`avoid`/u);
  assert.match(writer, /revisionBrief/u);
  assert.match(writer, /不读取或推断未传入的原始 Judge 对话/u);
});

test("one Writer owns draft, loop revision and feedback with a resumable conversation", () => {
  const manifest = JSON.parse(read(".codebuddy-plugin/plugin.json"));
  assert.ok(manifest.agents.includes("./agents/report-writer-v2.md"));
  assert.ok(!manifest.agents.some(p => p.includes("rewriter")));
  assert.ok(!fs.existsSync(path.join(root, "agents/report-rewriter-v2.md")));
  for (const agent of manifest.agents) assert.ok(fs.existsSync(path.join(root, agent)));
  const writer = read("agents/report-writer-v2.md");
  for (const mode of ["draft", "revise", "feedback"]) assert.ok(writer.includes(`mode=${mode}`));
  const instructionLink = writer.match(/\]\(([^)]+writing-instructions\.md)\)/u)?.[1];
  assert.ok(instructionLink);
  assert.equal(path.resolve(root, "agents", instructionLink), path.join(root, "skills/research-report-agent-v2/references/writing-instructions.md"));
  assert.match(writer, /首次写作前必须完整读取/u);
  assert.match(writer, /REPORT_WRITE_COMPLETED/u);
  const orchestration = read("skills/research-report-agent-v2/references/writer-orchestration.md");
  assert.match(orchestration, /resume=writerAgentId/u);
  assert.match(orchestration, /taskId.*不得冒充 agentId/u);
  assert.match(orchestration, /无法恢复该 ID/u);
  assert.match(orchestration, /重建一次 Writer/u);
  assert.match(orchestration, /不同报告不复用 writerAgentId/u);
  assert.match(orchestration, /已有 R0 不重写/u);
  const state = read("skills/research-report-agent-v2/references/state-and-scoring.md");
  const sample = JSON.parse(state.match(/```json\n([\s\S]*?)\n```/u)[1]);
  assert.equal(sample.writerAgentId, null);
  assert.equal(sample.deadlineAt, null);
  assert.ok(sample.status.split("|").includes("drafting"));
  for (const caller of ["SKILL.md", "references/loop-orchestration.md", "references/memory-orchestration.md", "references/evidence-orchestration.md"]) {
    assert.match(read(`skills/research-report-agent-v2/${caller}`), /writer-orchestration\.md/u);
  }
});

test("build emits a self-contained Expert without platform launchers", async () => {
  await import(`../scripts/build.mjs?test=${Date.now()}`);
  const target = path.join(root, "release/report-agent-v2-expert-0.3.0");
  const manifest = JSON.parse(fs.readFileSync(path.join(target, ".codebuddy-plugin/plugin.json"), "utf8"));

  assert.equal(manifest.name, "report-agent-v2");
  assert.equal(manifest.version, "0.3.0");
  assert.equal(fs.existsSync(path.join(target, "rubrics/base-rubrics.json")), true);
  assert.equal(fs.existsSync(path.join(target, "skills/research-report-agent-v2/SKILL.md")), true);
  for (const asset of ["agents/report-evidence-agent-v2.md", "resources/evidence/references/structured_data.schema.json", "resources/evidence/scripts/source_inventory.py"]) {
    assert.equal(fs.readFileSync(path.join(target, asset), "utf8"), read(asset));
  }
  assert.equal(fs.existsSync(path.join(target, "skills/research-report-agent-v2/references/state-and-scoring.md")), true);
  assert.equal(fs.existsSync(path.join(target, "scripts")), false);
  assert.equal(fs.existsSync(path.join(target, "hooks")), false);
  assert.equal(fs.existsSync(path.join(target, "skills/report-evidence-v2")), false);
});
