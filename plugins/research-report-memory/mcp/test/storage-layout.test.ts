import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { migrateStorageLayout } from "../src/storage-layout.ts";

test("legacy root storage moves into the unified product layout", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-layout-"));
  const legacyEpisode = path.join(dataDir, "episodes", "ep_legacy.json");
  const legacyRecord = path.join(dataDir, "records", "2026-08-18.jsonl");
  fs.mkdirSync(path.dirname(legacyEpisode), { recursive: true });
  fs.mkdirSync(path.dirname(legacyRecord), { recursive: true });
  fs.mkdirSync(path.join(dataDir, "conversations"));
  fs.mkdirSync(path.join(dataDir, "scene_blocks"));
  fs.writeFileSync(legacyEpisode, "{}\n");
  fs.writeFileSync(legacyRecord, "{}\n");

  try {
    await migrateStorageLayout(dataDir);
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l0-episodes", "ep_legacy.json")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l1-atoms", "records", "2026-08-18.jsonl")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "memorycore", "conversations")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "memorycore", "scene_blocks")));
    for (const oldName of ["episodes", "records", "conversations", "scene_blocks"]) {
      assert.equal(fs.existsSync(path.join(dataDir, oldName)), false, `${oldName} should not remain at data root`);
    }
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("mvp.10 storage and Git repository migrate without losing data", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-layout-mvp10-"));
  const episode = path.join(dataDir, "l0-writing-episodes", "ep_mvp10.json");
  const record = path.join(dataDir, "memorycore-l0-l1", "records", "2026-08-18.jsonl");
  const database = path.join(dataDir, "memorycore-l0-l1", "vectors.db");
  const gitHead = path.join(dataDir, "repositories", "personal", "default", ".git", "HEAD");
  const worktreeMarker = path.join(dataDir, "worktrees", "marker");
  for (const file of [episode, record, database, gitHead, worktreeMarker]) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, `${path.basename(file)}\n`);
  }

  try {
    await migrateStorageLayout(dataDir);
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l0-episodes", "ep_mvp10.json")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l1-atoms", "records", "2026-08-18.jsonl")));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "memorycore", "vectors.db")));
    assert.ok(fs.existsSync(path.join(dataDir, "l2-l3-memory", "personal", "default", ".git", "HEAD")));
    assert.ok(fs.existsSync(path.join(dataDir, "l2-l3-memory", "worktrees", "marker")));
    for (const oldName of ["l0-writing-episodes", "memorycore-l0-l1", "repositories", "worktrees"]) {
      assert.equal(fs.existsSync(path.join(dataDir, oldName)), false, `${oldName} should not remain at data root`);
    }
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("scheduled maintenance cannot perform the first legacy layout migration", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-layout-guard-"));
  try {
    fs.mkdirSync(path.join(dataDir, "episodes"));
    await assert.rejects(
      migrateStorageLayout(dataDir, { allowLegacyMigration: false }),
      /storage_layout_migration_deferred:episodes/u,
    );
    assert.ok(fs.existsSync(path.join(dataDir, "episodes")));
    assert.equal(fs.existsSync(path.join(dataDir, "l0-l1-memory")), false);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
