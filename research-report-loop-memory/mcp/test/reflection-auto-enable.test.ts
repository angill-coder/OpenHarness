import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { reflectionAutoEnablePlan } from "../src/reflection-auto-enable.ts";

test("Reflection is installed only after memory is enabled for a packaged plugin", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "reflection-auto-enable-"));
  const pluginRoot = path.join(temporary, "plugin");
  const dataDirectory = path.join(temporary, "memory");
  fs.mkdirSync(path.join(pluginRoot, "dist"), { recursive: true });
  fs.mkdirSync(path.join(pluginRoot, "scripts"), { recursive: true });
  fs.writeFileSync(path.join(pluginRoot, "scripts/install-reflection-macos.sh"), "#!/bin/sh\n");
  const moduleUrl = pathToFileURL(path.join(pluginRoot, "dist/memory-server.mjs")).href;

  try {
    const disabledPlan = reflectionAutoEnablePlan(moduleUrl, {
      RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDirectory,
    }, "darwin");
    assert.deepEqual(disabledPlan, { action: "skip", reason: "memory_disabled" });

    fs.mkdirSync(dataDirectory, { recursive: true });
    fs.writeFileSync(
      path.join(dataDirectory, "settings.json"),
      JSON.stringify({ schemaVersion: 1, memoryEnabled: true }),
    );
    const plan = reflectionAutoEnablePlan(moduleUrl, {
      RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDirectory,
    }, "darwin");
    assert.equal(plan.action, "install");
    if (plan.action === "install") {
      assert.equal(plan.command, "/bin/sh");
      assert.equal(plan.dataDirectory, dataDirectory);
    }

    const sourcePlan = reflectionAutoEnablePlan(
      pathToFileURL(path.join(pluginRoot, "mcp/src/server.ts")).href,
      { RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDirectory },
      "darwin",
    );
    assert.deepEqual(sourcePlan, { action: "skip", reason: "not_packaged_plugin" });
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});

test("A persisted user opt-out prevents Reflection from being re-enabled", () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "reflection-auto-disable-"));
  const pluginRoot = path.join(temporary, "plugin");
  const dataDirectory = path.join(temporary, "memory");
  fs.mkdirSync(path.join(pluginRoot, "dist"), { recursive: true });
  fs.mkdirSync(path.join(pluginRoot, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(dataDirectory, "reflection"), { recursive: true });
  fs.writeFileSync(path.join(pluginRoot, "scripts/install-reflection-windows.ps1"), "# installer\n");
  fs.writeFileSync(
    path.join(dataDirectory, "settings.json"),
    JSON.stringify({ schemaVersion: 1, memoryEnabled: true }),
  );
  fs.writeFileSync(
    path.join(dataDirectory, "reflection/schedule-settings.json"),
    JSON.stringify({ enabled: false }),
  );

  try {
    const plan = reflectionAutoEnablePlan(
      pathToFileURL(path.join(pluginRoot, "dist/memory-server.mjs")).href,
      { RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDirectory },
      "win32",
    );
    assert.deepEqual(plan, { action: "skip", reason: "disabled_by_user" });
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
