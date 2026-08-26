import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ReportLoopLauncher, resolveRunnerPaths, runnerCommand } from "../src/report-loop-launcher.ts";

test("launcher resolves the unchanged Python Runner from the plugin root", () => {
  const paths = resolveRunnerPaths();
  assert.equal(fs.existsSync(paths.runner), true);
  assert.match(paths.runner, /mcp[/\\]report_loop[/\\]runner\.py$/u);
  const unix = runnerCommand(paths, "darwin");
  assert.equal(unix.command, "sh");
  assert.equal(unix.usesJobEnvironment, false);
  const windows = runnerCommand(paths, "win32");
  assert.equal(windows.command, "cmd.exe");
  assert.deepEqual(windows.args, ["/d", "/c", paths.launcher, paths.runner, "--job"]);
  assert.equal(windows.usesJobEnvironment, false);
});

test("launcher rejects missing jobs and relays the Runner final JSON", async () => {
  const launcher = new ReportLoopLauncher();
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-launcher-"));
  const invalidJob = path.join(temporary, "invalid-job.json");
  fs.writeFileSync(invalidJob, JSON.stringify({ schemaVersion: 1 }));
  try {
    assert.equal((await launcher.run("relative-job.json", 5000)).reason, "report_loop_job_not_found");
    const result = await launcher.run(invalidJob, 15_000);
    assert.equal(result.status, "error");
    assert.equal(result.reason, "schemaVersion must be 2");
  } finally {
    await launcher.destroy();
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
