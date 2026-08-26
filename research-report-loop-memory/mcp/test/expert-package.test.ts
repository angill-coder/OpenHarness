import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("WorkBuddy Expert remains a thin wrapper around the existing plugin", () => {
  const agent = fs.readFileSync(path.join(root, "expert/report-expert-v1.md"), "utf8");
  const frontmatter = agent.match(/^---\r?\n([\s\S]*?)\r?\n---/u)?.[1] ?? "";
  const builder = fs.readFileSync(path.join(root, "scripts/build-expert.mjs"), "utf8");

  assert.match(frontmatter, /^name: report-expert-v1$/mu);
  assert.match(frontmatter, /^skills: \[research-report-loop\]$/mu);
  assert.doesNotMatch(frontmatter, /^tools:/mu);
  assert.match(builder, /scripts\/build-release\.mjs/u);
  assert.match(builder, /report-expert-v2/u);
  assert.match(builder, /expertPluginName = "report-loop"/u);
  assert.match(builder, /mcp__report-expert-v2__/u);
  assert.match(builder, /bin\/run-node/u);
  assert.match(builder, /bin\/run-node\.cmd/u);
  assert.match(builder, /hooks\.json/u);
  assert.doesNotMatch(builder, /expertType:\s*"team"/u);
});

test("packaged Expert Reflection uses the Expert root, MCP name, and launcher", () => {
  const completed = spawnSync(
    process.execPath,
    [path.join(root, "scripts/build-expert.mjs"), "--no-archive"],
    { cwd: root, encoding: "utf8" },
  );
  assert.equal(completed.status, 0, completed.stderr);

  const version = JSON.parse(
    fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
  ).version;
  const expertDir = path.join(root, "release", `report-loop-expert-${version}`);
  const macLauncher = fs.readFileSync(path.join(expertDir, "scripts/reflection-current.sh"), "utf8");
  const windowsLauncher = fs.readFileSync(path.join(expertDir, "scripts/reflection-current.ps1"), "utf8");
  const manifest = JSON.parse(
    fs.readFileSync(path.join(expertDir, ".codebuddy-plugin/plugin.json"), "utf8"),
  );
  const hooks = JSON.parse(fs.readFileSync(path.join(expertDir, "hooks.json"), "utf8"));

  assert.equal(fs.existsSync(path.join(expertDir, "scripts/disable-reflection-macos.sh")), true);
  assert.equal(fs.existsSync(path.join(expertDir, "scripts/disable-reflection-windows.ps1")), true);

  for (const launcher of [macLauncher, windowsLauncher]) {
    assert.match(launcher, /report-expert-v2/u);
    assert.match(launcher, /my-experts/u);
    assert.match(launcher, /report-loop/u);
    assert.match(launcher, /bin[\\/]run-node/u);
    assert.doesNotMatch(launcher, /research-report-loop-memory@/u);
    assert.doesNotMatch(launcher, /report-memory-v2/u);
    assert.doesNotMatch(launcher, /scripts[\\/]run-node\.(?:sh|cmd)/u);
  }
  for (const registrations of Object.values(hooks.hooks) as Array<Array<{ hooks: Array<{ command: string }> }>>) {
    const command = registrations[0].hooks[0].command;
    if (process.platform === "win32") {
      assert.match(command, /^cmd\.exe \/d \/c call "\$\{CODEBUDDY_PLUGIN_ROOT\}\\bin\\run-node\.cmd" /u);
      assert.match(command, /"\$\{CODEBUDDY_PLUGIN_ROOT\}\\bin\\capture-checkpoint\.mjs"/u);
      assert.doesNotMatch(command, /\/s|""|\bsh\b|run-node"/u);
    } else {
      assert.match(command, /^sh "\$\{CODEBUDDY_PLUGIN_ROOT\}\/bin\/run-node" /u);
      assert.match(command, /"\$\{CODEBUDDY_PLUGIN_ROOT\}\/bin\/capture-checkpoint\.mjs"/u);
      assert.doesNotMatch(command, /cmd\.exe|run-node\.cmd|\\bin\\/u);
    }
  }
  if (process.platform === "win32") {
    assert.equal(manifest.mcpServers["report-expert-v2"].command, "cmd.exe");
    assert.deepEqual(manifest.mcpServers["report-expert-v2"].args, [
      "/d",
      "/c",
      "${CODEBUDDY_PLUGIN_ROOT}\\bin\\run-node.cmd",
      "${CODEBUDDY_PLUGIN_ROOT}\\dist\\memory-server.mjs",
    ]);
  }
});
