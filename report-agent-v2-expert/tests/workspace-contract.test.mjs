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
  for (const group of ["agents", "skills"]) {
    for (const name of fs.readdirSync(path.join(root, group), {recursive: true}).filter(p => p.endsWith(".md"))) {
      const filename = path.join(root, group, name);
      for (const match of fs.readFileSync(filename, "utf8").matchAll(/\]\(([^)]+\.md)\)/g)) {
        assert.ok(fs.existsSync(path.resolve(path.dirname(filename), match[1])), `${name}: ${match[1]}`);
      }
    }
  }
});

test("new workspace separates delivery, immutable history and internal state", () => {
  const policy = read(`${skillRoot}/references/workspace-and-delivery.md`);
  const loop = read(`${skillRoot}/references/loop-orchestration.md`);
  const state = read(`${skillRoot}/references/state-and-scoring.md`);
  assert.match(policy, /用户指定位置优先/u);
  assert.match(policy, /不默认使用 WorkBuddy 会话目录/u);
  assert.match(policy, /目标不可写时，先确认/u);
  assert.match(policy, /恢复已有任务沿用已记录的原路径/u);
  assert.match(policy, /不改变记忆目录/u);
  assert.ok(policy.includes("Agent运行记录/本轮需求.md"));
  assert.ok(read(`${skillRoot}/references/writer-orchestration.md`).includes("Agent运行记录/本轮需求.md"));
  assert.ok(read(`${skillRoot}/references/evidence-orchestration.md`).includes("Agent运行记录/本轮论据快照/r001"));
  assert.match(policy, /可见的过程记录目录/u);
  for (const artifact of ["历史版本/v0-初稿.md", "历史版本/v1.md", "历史版本/版本说明.md", "报告主题.md"]) {
    assert.ok(loop.includes(artifact), artifact);
  }
  for (const artifact of ["run-state.json", "judgments/<version>.json", "revision-briefs/<version>.json"]) {
    assert.ok(state.includes(`Agent运行记录/评测与改写记录/${artifact}`), artifact);
  }
  assert.ok(loop.includes("Agent运行记录/评测与改写记录/resolution-plan.json"));
  for (const group of ["agents", "skills"]) {
    for (const name of fs.readdirSync(path.join(root, group), {recursive: true}).filter(p => p.endsWith(".md"))) {
      assert.doesNotMatch(read(`${group}/${name}`), /source\/structured_data\.json|\.report-loop-v2\/|\.report-agent\/|inputs\.md|`versions\/|`summary\.md`/u, name);
    }
  }
  assert.match(read("skills/report-evidence-v2/references/output-contract.md"), /不另建 snapshot 文件/u);
});
