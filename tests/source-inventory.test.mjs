import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("source fingerprints detect incremental changes without model calls", () => {
  const executable = [process.env.PYTHON, "python3", "python"].filter(Boolean).find(command => {
    return spawnSync(command, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"], { timeout: 10000 }).status === 0;
  });
  assert.ok(executable, "Tests require Python 3.9+ (standard library only)");
  const script = path.join(path.dirname(fileURLToPath(import.meta.url)), "source_inventory_test.py");
  const result = spawnSync(executable, ["-B", script, "-v"], { encoding: "utf8", timeout: 30000 });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
