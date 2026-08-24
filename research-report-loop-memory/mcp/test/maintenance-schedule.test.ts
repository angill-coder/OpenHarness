import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("Dreaming is scheduled daily at 16:30 with an isolated Memory MCP", () => {
  const plist = fs.readFileSync(
    path.join(root, "scripts/maintenance-launchagent.plist.template"),
    "utf8",
  );
  const runner = fs.readFileSync(
    path.join(root, "scripts/run-memory-maintenance-workbuddy.sh"),
    "utf8",
  );
  const installer = fs.readFileSync(
    path.join(root, "scripts/install-maintenance-macos.sh"),
    "utf8",
  );
  assert.match(plist, /<key>Hour<\/key><integer>16<\/integer>/u);
  assert.match(plist, /<key>Minute<\/key><integer>30<\/integer>/u);
  assert.match(installer, /com\.research-report\.loop-memory\.maintenance/u);
  assert.match(runner, /dist\/memory-server\.mjs/u);
  assert.match(runner, /--strict-mcp-config/u);
  assert.match(runner, /operation=maintenance/u);
  assert.match(runner, /只提交增量修改/u);
  assert.doesNotMatch(runner, /research-report-loop__report_loop/u);
});
