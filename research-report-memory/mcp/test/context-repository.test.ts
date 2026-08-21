import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ContextRepository } from "../src/context-repository.ts";

test("visible Markdown is the canonical L2/L3 source and filenames are uniform", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-context-"));
  try {
    const repository = new ContextRepository(dataDir);
    await repository.initialize();
    await repository.applyDocuments([
      {
        layer: "L2", scope: "audience", scopeValue: "管理委员会", title: "面向管理委员会的写作要求", description: "受众写作要求。",
        items: [{ id: "audience-m-conclusion", summary: "优先呈现业务结论。", rules: ["第一页先给决策含义。"], sourceL1Ids: ["m_source_1"] }],
      },
      {
        layer: "L3", scope: "audience", scopeValue: "管理委员会", title: "面向管理委员会的评判标准", description: "受众评判标准。",
        items: [{ id: "audience-m-rubric", criterion: "是否给出决策含义", pass: "第一页明确给出", fail: "只陈述过程", status: "active", sourceL1Ids: ["m_source_1"] }],
      },
    ], "canonical-markdown-test");

    const audienceDir = path.join(repository.root, "audiences", "管理委员会");
    const l2Path = path.join(audienceDir, "l2-context.md");
    const l3Path = path.join(audienceDir, "l3-rubrics.md");
    assert.ok(fs.existsSync(l2Path));
    assert.ok(fs.existsSync(l3Path));
    assert.ok(fs.existsSync(path.join(repository.root, "system", "l2-context.md")));
    assert.ok(fs.existsSync(path.join(repository.root, "system", "l3-rubrics.md")));
    assert.equal(fs.existsSync(path.join(audienceDir, "context.md")), false);
    assert.doesNotMatch(fs.readFileSync(l2Path, "utf8"), /memory-item/u);
    assert.match(fs.readFileSync(l2Path, "utf8"), /<!-- sources: m_source_1 -->/u);

    const edited = fs.readFileSync(l2Path, "utf8")
      .replace("优先呈现业务结论。", "优先呈现北极星指标与业务结论。")
      .replace("第一页先给决策含义。", "第一页先给北极星指标变化和决策含义。");
    fs.writeFileSync(l2Path, edited);
    execFileSync("git", ["add", "--all"], { cwd: repository.root });
    execFileSync("git", ["commit", "-m", "test: edit visible Markdown"], { cwd: repository.root });
    const documents = await repository.recall({ audience: "管理委员会" });
    const audienceL2 = documents.find((document) => document.path.endsWith("audiences/管理委员会/l2-context.md"));
    assert.equal(audienceL2?.items[0] && "summary" in audienceL2.items[0] ? audienceL2.items[0].summary : "", "优先呈现北极星指标与业务结论。");
    assert.deepEqual(audienceL2?.items[0] && "rules" in audienceL2.items[0] ? audienceL2.items[0].rules : [], ["第一页先给北极星指标变化和决策含义。"]);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("document patches update only targeted items and preserve untouched content", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-context-patch-"));
  try {
    const repository = new ContextRepository(dataDir);
    await repository.initialize();
    await repository.applyDocuments([{
      layer: "L2", scope: "core", title: "Writing Core", description: "跨项目写作要求。",
      items: [
        { id: "core-keep", summary: "保留内容。", rules: ["这条内容不应被改动。"], sourceL1Ids: ["m_keep"] },
        { id: "core-update", summary: "旧摘要。", rules: ["旧规则。"], sourceL1Ids: ["m_old"] },
      ],
    }], "patch-seed");

    const result = await repository.applyDocumentPatches([{
      layer: "L2", scope: "core",
      upsertItems: [{ id: "core-update", summary: "新摘要。", rules: ["新规则。"], sourceL1Ids: ["m_new"] }],
    }], "patch-update");
    assert.deepEqual(result.changedPaths, ["system/l2-context.md"]);

    const document = (await repository.recall({})).find((value) => value.layer === "L2" && value.scope === "core");
    assert.equal(document?.items.length, 2);
    const keep = document?.items.find((item) => item.id === "core-keep");
    const updated = document?.items.find((item) => item.id === "core-update");
    assert.equal(keep && "summary" in keep ? keep.summary : "", "保留内容。");
    assert.equal(updated && "summary" in updated ? updated.summary : "", "新摘要。");
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("legacy filenames and hidden JSON migrate without overriding visible prose", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-migration-"));
  try {
    const repository = new ContextRepository(dataDir);
    await repository.initialize();
    const systemDir = path.join(repository.root, "system");
    fs.rmSync(path.join(systemDir, "l2-context.md"));
    fs.writeFileSync(path.join(systemDir, "writing-core.md"), `---
description: "跨项目要求。"
---

# Writing Core

## core-visible-authority
<!-- memory-item {"id":"core-visible-authority","summary":"隐藏旧摘要不应生效。","rules":["隐藏旧规则不应生效。"],"sourceL1Ids":["m_legacy_source"]} -->

可见摘要是唯一权威内容。

### Rules
- 可见规则必须被解析。
`);
    execFileSync("git", ["add", "--all"], { cwd: repository.root });
    execFileSync("git", ["commit", "-m", "test: create legacy document"], { cwd: repository.root });

    const reopened = new ContextRepository(dataDir);
    await reopened.initialize();
    const migratedPath = path.join(systemDir, "l2-context.md");
    const migrated = fs.readFileSync(migratedPath, "utf8");
    assert.equal(fs.existsSync(path.join(systemDir, "writing-core.md")), false);
    assert.doesNotMatch(migrated, /memory-item|隐藏旧摘要|隐藏旧规则/u);
    assert.match(migrated, /<!-- sources: m_legacy_source -->/u);
    const documents = await reopened.recall({});
    const item = documents.find((document) => document.layer === "L2")?.items[0];
    assert.equal(item && "summary" in item ? item.summary : "", "可见摘要是唯一权威内容。");
    assert.deepEqual(item && "rules" in item ? item.rules : [], ["可见规则必须被解析。"]);
    assert.match(execFileSync("git", ["log", "-1", "--pretty=%s"], { cwd: repository.root, encoding: "utf8" }), /migrate Markdown schema and filenames/u);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
