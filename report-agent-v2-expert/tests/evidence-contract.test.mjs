import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");

test("only the main skill is registered and Evidence is a standalone agent", () => {
  const manifest = JSON.parse(read(".codebuddy-plugin/plugin.json"));
  assert.equal(manifest.version, JSON.parse(read("package.json")).version);
  assert.ok(manifest.agents.includes("./agents/report-evidence-agent-v2.md"));
  assert.deepEqual(manifest.skills, ["./skills/research-report-agent-v2"]);
  const agent = read("agents/report-evidence-agent-v2.md");
  assert.doesNotMatch(agent, /^skills:/mu);
  assert.ok(!fs.existsSync(path.join(root, "skills/report-evidence-v2/SKILL.md")));
  assert.match(agent, /\.\.\/resources\/evidence\/references\/cleaning-rules.md/u);
  assert.match(agent, /## 输入/u);
  assert.match(agent, /## 更新判断/u);
  assert.doesNotMatch(agent, /^memory:/mu);
  assert.match(agent, /不调用外部模型 CLI/u);
});

test("evidence preparation is delegated by default before confirmation, updates are not Memory", () => {
  const skill = read("skills/research-report-agent-v2/SKILL.md");
  assert.ok(skill.indexOf("委派 `report-evidence-agent-v2`") < skill.indexOf("### 第 1 步"));
  assert.match(skill, /少量素材也走这一步/u);
  assert.match(skill, /等待返回后再确认写作输入/u);
  assert.doesNotMatch(skill, /按需委派|不强制调用/u);
  assert.match(skill, /事实更新不作为写作偏好存入 Memory/u);
  const contract = read("skills/research-report-agent-v2/references/evidence-orchestration.md");
  assert.match(contract, /第 0 步默认先委派/u);
  assert.match(contract, /EVIDENCE_UNCHANGED.*复用/u);
  assert.doesNotMatch(contract, /可选的数据准备|不必额外跑一次/u);
  assert.match(read("agents/report-agent-v2.md"), /第 0 步先委派 `report-evidence-agent-v2`/u);
  assert.match(contract, /已有表就 `operation=update`/u);
  assert.match(contract, /不热替换其输入表/u);
  assert.match(contract, /旧 Judgment 不复用/u);
  assert.match(contract, /EVIDENCE_PARTIAL/u);
  assert.match(contract, /原有主 Agent 解析素材流程/u);
});

test("evidence contract preserves stable IDs, sources and old versions", () => {
  const skill = read("agents/report-evidence-agent-v2.md");
  assert.match(skill, /不复用已移除 ID/u);
  assert.match(skill, /未变论据的 ID 与内容不变/u);
  assert.match(skill, /拒绝覆盖/u);
  assert.match(skill, /不创建空版本/u);
  const rules = read("resources/evidence/references/cleaning-rules.md");
  assert.match(rules, /问题中的数字也不能冒充/u);
  assert.match(rules, /不能.*用户迁移/u);
  assert.match(rules, /百分比、百分点/u);
  const schema = JSON.parse(read("resources/evidence/references/structured_data.schema.json"));
  assert.deepEqual(schema.required, ["schema", "case_id", "items", "unresolved"]);
  assert.deepEqual(schema.properties.items.items.required, ["id", "type", "source_ref", "content"]);
  assert.equal(schema.properties.items.minItems, 1);
  assert.equal(schema.additionalProperties, false);
  assert.equal(schema.properties.items.items.additionalProperties, false);
});

test("shared evidence is version-bound without workspace data snapshots", () => {
  const main = read("skills/research-report-agent-v2/SKILL.md");
  const orchestration = read("skills/research-report-agent-v2/references/evidence-orchestration.md");
  const evidence = read("agents/report-evidence-agent-v2.md");
  const output = read("resources/evidence/references/output-contract.md");
  const loop = read("skills/research-report-agent-v2/references/loop-orchestration.md");
  assert.ok(main.includes("`structured_data.json`"));
  assert.match(orchestration, /不要只查当前工作区/u);
  assert.match(orchestration, /素材目录\/structured_data.json/u);
  assert.match(orchestration, /不追加子目录/u);
  assert.match(orchestration, /扫描原始素材时排除 `structured_data.json`/u);
  assert.match(evidence, /`workDir`/u);
  assert.match(output, /EVIDENCE_UNCHANGED.*不改写共享文件/u);
  assert.match(output, /不在工作区保存.*副本或 previous 备份/u);
  assert.match(output, /共享文件仍与开始时一致.*重读校验/u);
  assert.match(output, /并发变更或写入失败.*返回失败/u);
  assert.match(loop, /共享 `structured_data.json`.*dataVersion\/dataSha256.*不保存数据快照/u);
  assert.match(loop, /指纹变化时停止后续派发/u);
  assert.match(loop, /相对 `source_ref` 按原始素材根目录解析/u);
  assert.doesNotMatch(evidence, /不覆盖用户原来的 `structured_data.json`|全新版本 `structured_data.json` 绝对路径/u);
});

test("evidence fixtures and schema remain independent of the OpenHarness checkout", () => {
  const previous = JSON.parse(read("tests/fixtures/evidence/previous.json"));
  assert.equal(previous.items.length, 3);
  assert.equal(new Set(previous.items.map((i) => i.id)).size, 3);
  assert.match(read("tests/fixtures/evidence/update.md"), /10小时/u);
  const hash = crypto.createHash("sha256").update(read("resources/evidence/references/structured_data.schema.json")).digest("hex");
  // Exact copy of OpenHarness's structured_data.schema.json; no source checkout required.
  assert.equal(hash, "7565c93231d101e0b184df284feb31607c9028e9d2266e3daf9583754f025e67");
});

test("source scanning precedes incremental review and confirmation follows evidence publication", () => {
  const skill = read("agents/report-evidence-agent-v2.md");
  const contract = read("resources/evidence/references/source-changes.md");
  assert.match(skill, /用户无需指出新增/u);
  assert.match(skill, /sourceRoot/u);
  assert.match(contract, /SHA-256/u);
  assert.match(contract, /fullReviewRequired=true/u);
  assert.match(contract, /全部变化来源已经成功处理后/u);
  assert.match(contract, /解析失败.*不 confirm/u);
  assert.match(contract, /不能跳过这类明确更正/u);
  assert.match(contract, /没有可用 Python.*不临时安装环境/u);
  assert.match(read("skills/research-report-agent-v2/references/workspace-and-delivery.md"), /数据版本说明.md/u);
});

test("follow-up feedback routes by impact while preserving explicit user choices", () => {
  const contract = read("skills/research-report-agent-v2/references/evidence-orchestration.md");
  assert.match(contract, /先在普通回复中简述.*影响哪些内容/u);
  assert.match(contract, /writer-orchestration\.md#反馈分流/u);
  assert.match(contract, /改变主要结论或需要整体重写时启动新 Loop/u);
  assert.match(contract, /明确要求“只更新论据”.*报告不动，不调用 Writer 或 Judge/u);
  assert.match(contract, /不重问开场问题/u);
  assert.match(contract, /不为重新确认 hypothesis 常规暂停/u);
  assert.match(contract, /且没有其他修改或重评要求.*结束/u);
  assert.doesNotMatch(contract, /AskUserQuestion|简短确认以下选择|意图不明确时简短确认/u);
  const skill = read("skills/research-report-agent-v2/SKILL.md");
  assert.match(skill, /重大修改.*启动新一轮 Report Loop，小型修改直接 Rewrite/u);
  const writer = read("skills/research-report-agent-v2/references/writer-orchestration.md");
  assert.match(writer, /重大修改 → 新 Loop.*通篇改写.*分析方向/u);
  assert.match(writer, /根据修改对报告核心观点、分析方向和整体结构的影响选择路径/u);
  assert.match(writer, /小型修改 → 直接 Rewrite.*局部措辞.*不影响主要结论/u);
  assert.match(writer, /重新评测或“只修改、不评测”时按其要求/u);
  assert.match(writer, /直接建立新 Loop，不先另做一次 feedback 改写/u);
  assert.match(read("agents/report-agent-v2.md"), /重大修改启动新 Loop，小型修改直接 Rewrite/u);
  assert.match(read("skills/research-report-agent-v2/references/memory-orchestration.md"), /只针对本次用户反馈 Capture 一次/u);
});

test("rejudging updated materials creates fresh state and preserves the previous report", () => {
  const contract = read("skills/research-report-agent-v2/references/evidence-orchestration.md");
  const writer = read("skills/research-report-agent-v2/references/writer-orchestration.md");
  const prompt = read("agents/report-writer-v2.md");
  assert.match(contract, /新的 `loop-序号-日期时间\/`.*新的 drafting 状态/u);
  assert.match(contract, /不继承旧评分、旧采纳结果或停止计数/u);
  assert.match(contract, /旧运行和交付报告保持不动/u);
  assert.match(writer, /可续用原 Writer.*mode=draft.*baselineReportPath/u);
  assert.match(writer, /实际 Writer ID 保存到新运行状态/u);
  assert.match(prompt, /baselineReportPath.*本轮用户要求及有效论据.*不覆盖基线/u);
});
