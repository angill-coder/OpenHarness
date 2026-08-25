import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RubricRepository } from "../src/rubric-repository.ts";
import { scopeStorageKey } from "../src/scope-paths.ts";

const item = (id: string, statement: string, source = `atom-${id}`) => ({
  id, statement, status: "active" as const, sourceL1Ids: [source],
});

test("L2B stores independent Git-backed Memory Rubrics and upserts in place", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    const first = await repository.applyPatches([{ scope: "core", upsertItems: [item("MR-SUMMARY", "摘要保持精简。", "atom-1")] }], "seed");
    assert.equal(first.rubricSetVersion, "v1");
    await repository.applyPatches([{ scope: "core", upsertItems: [{ ...item("MR-SUMMARY", "摘要控制在 2–3 行。", "atom-1"), sourceL1Ids: ["atom-1", "atom-2"] }] }], "update");
    const document = (await repository.recall({}))[0];
    assert.equal(document.schemaVersion, 3);
    assert.deepEqual(document.rubrics, [{ id: "MR-SUMMARY", statement: "摘要控制在 2–3 行。", status: "active", sourceL1Ids: ["atom-1", "atom-2"] }]);
    const view = fs.readFileSync(path.join(repository.root, "views", "rubric-set.md"), "utf8");
    assert.match(view, /Statement: 摘要控制在 2–3 行/u);
    assert.doesNotMatch(view, /Criterion|Operation|Dimension/u);
    assert.match(execFileSync("git", ["log", "-1", "--pretty=%s"], { cwd: repository.root, encoding: "utf8" }), /update L2B rubrics/u);
  } finally { fs.rmSync(dataDir, { recursive: true, force: true }); }
});

test("same-scope patches accumulate and scopes remain isolated", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-scope-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    await repository.applyPatches([
      { scope: "core", upsertItems: [item("MR-C1", "核心规则一。")] },
      { scope: "core", upsertItems: [item("MR-C2", "核心规则二。")] },
      { scope: "audience", scopeValue: "管理委员会", upsertItems: [item("MR-A", "先给讨论项。", "atom-a")] },
      { scope: "project", scopeValue: "时长分析", upsertItems: [item("MR-P", "拆解频次和单次时长。", "atom-p")] },
    ], "scoped");
    const documents = await repository.recall({ audience: "管理委员会", project: "时长分析" });
    assert.deepEqual(documents.map((value) => value.scope).sort(), ["audience", "core", "project"]);
    assert.deepEqual(documents.find((value) => value.scope === "core")?.rubrics.map((value) => value.id).sort(), ["MR-C1", "MR-C2"]);
  } finally { fs.rmSync(dataDir, { recursive: true, force: true }); }
});

test("scope path hashes prevent readable-slug collisions", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-collision-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    const values = ["A/B", "A-B", "A B"];
    assert.equal(new Set(values.map(scopeStorageKey)).size, 3);
    for (const [index, value] of values.entries()) await repository.applyPatches([{ scope: "project", scopeValue: value, upsertItems: [item(`MR-P-${index}`, `只适用于 ${value}。`)] }], `scope-${index}`);
    for (const [index, value] of values.entries()) {
      const project = (await repository.recall({ project: value })).find((entry) => entry.scope === "project");
      assert.deepEqual(project?.rubrics.map((entry) => entry.id), [`MR-P-${index}`]);
    }
  } finally { fs.rmSync(dataDir, { recursive: true, force: true }); }
});

test("legacy schema v2 is read as independent schema v3 without mutating history", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-legacy-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    const target = path.join(repository.root, "system", "rubrics.json");
    fs.writeFileSync(target, `${JSON.stringify({ schemaVersion: 2, scope: "core", rubrics: [{ id: "MR-OLD", desc: "旧版摘要要求。", status: "active", sourceL1Ids: ["atom-old"] }] }, null, 2)}\n`);
    execFileSync("git", ["add", "--all"], { cwd: repository.root });
    execFileSync("git", ["commit", "-m", "seed legacy"], { cwd: repository.root });
    const document = (await repository.recall({}))[0];
    assert.equal(document.schemaVersion, 3);
    assert.equal(document.rubrics[0].statement, "旧版摘要要求。");
  } finally { fs.rmSync(dataDir, { recursive: true, force: true }); }
});

test("recall rejects mismatched stored scopeValue", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-l2b-mismatch-"));
  try {
    const repository = new RubricRepository(dataDir);
    await repository.initialize();
    const relative = `projects/${scopeStorageKey("A/B")}/rubrics.json`;
    const target = path.join(repository.root, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, `${JSON.stringify({ schemaVersion: 3, scope: "project", scopeValue: "A-B", rubrics: [] }, null, 2)}\n`);
    execFileSync("git", ["add", "--all"], { cwd: repository.root });
    execFileSync("git", ["commit", "-m", "seed mismatch"], { cwd: repository.root });
    await assert.rejects(repository.recall({ project: "A/B" }), /rubric_scope_value_mismatch/u);
  } finally { fs.rmSync(dataDir, { recursive: true, force: true }); }
});
