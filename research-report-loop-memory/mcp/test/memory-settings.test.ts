import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { readMemorySettings, writeMemorySettings } from "../src/memory-settings.ts";

test("Report Memory is enabled by default and persists an explicit user setting", (t) => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "report-memory-settings-"));
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));

  assert.deepEqual(readMemorySettings(dataDir), { schemaVersion: 1, memoryEnabled: true });
  assert.equal(writeMemorySettings(true, dataDir).memoryEnabled, true);
  assert.equal(readMemorySettings(dataDir).memoryEnabled, true);
  assert.equal(writeMemorySettings(false, dataDir).memoryEnabled, false);
  assert.equal(readMemorySettings(dataDir).memoryEnabled, false);
});
