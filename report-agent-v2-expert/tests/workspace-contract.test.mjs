import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const skillRoot = "skills/research-report-agent-v2";
const read = name => fs.readFileSync(path.join(root, name), "utf8");

test("workspace policy is loaded before evidence and all linked references exist", () => {
  const skill = read(`${skillRoot}/SKILL.md`);
  assert.ok(skill.indexOf("references/workspace-and-delivery.md") < skill.indexOf("委派 `report-evidence-agent-v2`"));
  for (const group of ["agents", "skills", "resources"]) {
    for (const name of fs.readdirSync(path.join(root, group), {recursive: true}).filter(p => p.endsWith(".md"))) {
      const filename = path.join(root, group, name);
      for (const match of fs.readFileSync(filename, "utf8").matchAll(/\]\(([^)]+\.md)\)/g)) {
        assert.ok(fs.existsSync(path.resolve(path.dirname(filename), match[1])), `${name}: ${match[1]}`);
      }
    }
  }
});

test("flat delivery versions and hidden per-loop candidates use distinct paths", () => {
  const policy = read(`${skillRoot}/references/workspace-and-delivery.md`);
  const loop = read(`${skillRoot}/references/loop-orchestration.md`);
  const state = read(`${skillRoot}/references/state-and-scoring.md`);
  assert.match(policy, /用户指定位置优先/u);
  assert.match(policy, /不默认使用 WorkBuddy 会话目录/u);
  assert.match(policy, /目标不可写时，先确认/u);
  assert.match(policy, /恢复已有任务沿用已记录的原路径/u);
  assert.match(policy, /不改变记忆目录/u);
  assert.ok(read(`${skillRoot}/references/writer-orchestration.md`).includes("`本轮需求.md`"));
  assert.ok(read(`${skillRoot}/references/evidence-orchestration.md`).includes("报告目录/.report-agent/<报告标识>/素材处理/r001"));
  for (const artifact of ["候选报告/R0.md", "候选报告/R1.md", "<报告主题>-vN.md", "版本说明.md"]) {
    assert.ok(loop.includes(artifact), artifact);
  }
  for (const artifact of ["run-state.json", "judgments/<version>.json", "revision-briefs/<version>.json"]) {
    assert.ok(state.includes(`评测与改写记录/${artifact}`), artifact);
  }
  assert.ok(loop.includes("评测与改写记录/resolution-plan.json"));
  for (const group of ["agents", "skills"]) {
    for (const name of fs.readdirSync(path.join(root, group), {recursive: true}).filter(p => p.endsWith(".md"))) {
      assert.doesNotMatch(read(`${group}/${name}`), /source\/structured_data\.json|\.report-loop-v2\/|Agent运行记录\/|历史版本\/|<报告主题>-v0\.md|\bV0\b|inputs\.md|`versions\/|`summary\.md`/u, name);
    }
  }
  assert.match(policy, /日期时间在本轮开始时确定/u);
  assert.match(policy, /数据版本说明.md/u);
  assert.doesNotMatch(policy, /├── 本轮论据快照/u);
  assert.match(policy, /dataVersion\/dataSha256/u);
  assert.match(policy, /Windows.*Hidden/u);
  assert.match(policy, /不新建 Loop 或反馈修订记录目录/u);
  assert.match(policy, /正文未变.*不复制新报告/u);
  assert.match(policy, /同一报告.*最大序号加一/u);
  assert.match(policy, /每轮 Loop 独立从 `R0`/u);
  assert.match(policy, /RN 与 vN 没有数字对应关系/u);
  const example = policy.match(/```text\n([\s\S]*?)\n```/u)[1];
  for (const v of [1, 2, 3]) assert.match(example, new RegExp(`<报告主题>-v${v}\\.md +如`, 'u'));
  assert.doesNotMatch(example, /历史版本|反馈修订记录|<报告主题>\.md/u);
  const stateExample = JSON.parse(state.match(/```json\n([\s\S]*?)\n```/u)[1]);
  assert.equal(stateExample.nextVersion, 1);
  assert.equal(stateExample.delivery, null);
  assert.ok(stateExample.reportDirectory && stateExample.loopDirectory);
  assert.match(read("resources/evidence/references/output-contract.md"), /不在工作区保存.*副本或 previous 备份/u);
});
