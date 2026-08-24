import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RubricRepository } from "../src/rubric-repository.ts";
import { scopeStorageKey } from "../src/scope-paths.ts";

test("L2B uses Git-backed judge-ready JSON and incremental patches", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    const initialHead = await repository.head();
    const first = await repository.applyPatches([{
      scope: "core",
      upsertItems: [{
        id: "MR-EXPRESSION-SUMMARY",
        criterionKey: "structure.s1",
        operation: "extend",
        dimension: "structure",
        label: "摘要精简",
        desc: "摘要控制在2–3行，仅呈现核心观点与关键推导逻辑。",
        effect: "摘要冗长会稀释核心结论。",
        requirements: [{ key: "summary.max_lines", text: "摘要控制在2–3行" }],
        redline: false,
        status: "active",
        sourceL1Ids: ["m_source_1"],
      }],
    }], "seed-summary-rubric");
    assert.notEqual(first.head, initialHead);
    assert.equal(first.rubricSetVersion, "v1");
    assert.deepEqual(first.changedPaths, ["system/rubrics.json"]);

    await repository.applyPatches([{
      scope: "core",
      upsertItems: [{
        id: "MR-EXPRESSION-SUMMARY",
        criterionKey: "structure.s1",
        operation: "extend",
        dimension: "structure",
        label: "摘要精简",
        desc: "摘要控制在2–3行，仅呈现核心结论及其推导逻辑。",
        effect: "摘要冗长会增加管理者阅读成本。",
        requirements: [{ key: "summary.max_lines", text: "摘要控制在2–3行" }],
        redline: false,
        status: "active",
        sourceL1Ids: ["m_source_1", "m_source_2"],
      }],
    }], "merge-summary-evidence");

    const document = (await repository.recall({}))[0];
    assert.equal(document.rubrics.length, 1, "upsert replaces the item instead of appending a duplicate");
    assert.deepEqual(document.rubrics[0].sourceL1Ids, ["m_source_1", "m_source_2"]);
    assert.equal((await repository.manifest()).version, "v2");
    const jsonPath = path.join(repository.root, "system", "rubrics.json");
    assert.ok(fs.existsSync(jsonPath));
    const markdownView = fs.readFileSync(path.join(repository.root, "views", "rubric-set.md"), "utf8");
    assert.match(markdownView, /Research Report Rubric Set v2/u);
    assert.match(markdownView, /Criterion: `structure\.s1`/u);
    assert.equal(fs.existsSync(path.join(repository.root, "system", "l2-context.md")), false);
    assert.match(execFileSync("git", ["log", "-1", "--pretty=%s"], { cwd: repository.root, encoding: "utf8" }), /update L2B rubrics/u);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("L2B repository keeps core, audience and project scopes separate", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-scope-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    await repository.applyPatches([{
      scope: "audience",
      scopeValue: "管理委员会",
      upsertItems: [{
        id: "MR-AUDIENCE-DECISION",
        criterionKey: "structure.decision_first",
        operation: "add",
        dimension: "structure",
        label: "讨论项前置",
        desc: "报告开头直接给出需要受众讨论或决策的问题。",
        effect: "讨论项后置会降低决策效率。",
        redline: false,
        status: "active",
        sourceL1Ids: ["m_audience"],
      }],
    }, {
      scope: "project",
      scopeValue: "DS时长分析",
      upsertItems: [{
        id: "MR-PROJECT-METRIC",
        criterionKey: "insight.duration_decomposition",
        operation: "add",
        dimension: "insight",
        label: "时长驱动拆解",
        desc: "核心结论分别说明频次与单次时长的贡献。",
        effect: "不拆解会掩盖增长机制。",
        redline: false,
        status: "active",
        sourceL1Ids: ["m_project"],
      }],
    }], "scoped-rubrics");

    const documents = await repository.recall({ audience: "管理委员会", project: "DS时长分析" });
    assert.deepEqual(documents.map((value) => value.scope).sort(), ["audience", "core", "project"]);
    assert.equal(documents.flatMap((value) => value.rubrics).length, 2);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("scope paths remain isolated when readable slugs collide", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-collision-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    const values = ["A/B", "A-B", "A B"];
    assert.equal(new Set(values.map(scopeStorageKey)).size, 3);
    for (const [index, scopeValue] of values.entries()) {
      await repository.applyPatches([{
        scope: "project",
        scopeValue,
        upsertItems: [{
          id: `MR-PROJECT-${index}`,
          criterionKey: `structure.project_${index}`,
          operation: "add",
          dimension: "structure",
          label: `项目规则${index}`,
          desc: `只适用于项目 ${scopeValue}`,
          effect: "不得串入其他项目。",
          redline: false,
          status: "active",
          sourceL1Ids: [`m_project_${index}`],
        }],
      }], `scope-collision-${index}`);
    }

    for (const [index, scopeValue] of values.entries()) {
      const project = (await repository.recall({ project: scopeValue }))
        .find((value) => value.scope === "project");
      assert.equal(project?.scopeValue, scopeValue);
      assert.deepEqual(project?.rubrics.map((item) => item.id), [`MR-PROJECT-${index}`]);
    }
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("recall rejects a document whose stored scopeValue does not match the requested project", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-mismatch-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    const relativePath = `projects/${scopeStorageKey("A/B")}/rubrics.json`;
    const target = path.join(repository.root, relativePath);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, `${JSON.stringify({
      schemaVersion: 2,
      scope: "project",
      scopeValue: "A-B",
      rubrics: [],
    }, null, 2)}\n`);
    execFileSync("git", ["add", "--all"], { cwd: repository.root });
    execFileSync("git", ["commit", "-m", "seed mismatched scope"], { cwd: repository.root });

    await assert.rejects(
      repository.recall({ project: "A/B" }),
      /rubric_scope_value_mismatch/u,
    );
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
