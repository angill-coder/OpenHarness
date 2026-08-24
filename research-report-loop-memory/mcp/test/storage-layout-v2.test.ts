import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { migrateStorageLayout } from "../src/storage-layout.ts";

test("V2 creates only L0/L1 MemoryCore and L2B storage roots", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-v2-layout-"));
  try {
    await migrateStorageLayout(dataDir);
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l0-episodes")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l1-atoms")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "memorycore")));
    assert.ok(fs.existsSync(path.join(dataDir, "l2b-rubrics")));
    assert.equal(fs.existsSync(path.join(dataDir, "l2-l3-memory")), false);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("legacy L0/L1 roots migrate but V1 high-level repositories are never adopted", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-v2-migrate-"));
  const episode = path.join(dataDir, "episodes", "ep_legacy.json");
  const record = path.join(dataDir, "records", "legacy.jsonl");
  const oldHighLevel = path.join(dataDir, "repositories", "personal", "default", "system", "l3-rubrics.md");
  for (const file of [episode, record, oldHighLevel]) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "legacy\n");
  }
  try {
    await migrateStorageLayout(dataDir);
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l0-episodes", "ep_legacy.json")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l1-atoms", "records", "legacy.jsonl")));
    assert.ok(fs.existsSync(oldHighLevel), "V1 high-level data must remain untouched");
    assert.equal(fs.existsSync(path.join(dataDir, "l2b-rubrics", "personal")), false, "layout creation must not convert V1 rubrics");
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("scheduled maintenance cannot perform first L0/L1 legacy migration", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-v2-layout-guard-"));
  try {
    fs.mkdirSync(path.join(dataDir, "episodes"));
    await assert.rejects(
      migrateStorageLayout(dataDir, { allowLegacyMigration: false }),
      /storage_layout_migration_deferred:episodes/u,
    );
    assert.ok(fs.existsSync(path.join(dataDir, "episodes")));
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
