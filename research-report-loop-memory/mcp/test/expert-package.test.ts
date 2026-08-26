import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function nodeWithSqlite(): string {
  const candidates = [process.env.CODEBUDDY_NODE_BIN, process.execPath];
  const configRoot = process.env.WORKBUDDY_CONFIG_DIR
    || process.env.CODEBUDDY_CONFIG_DIR
    || path.join(os.homedir(), ".workbuddy");
  const versionsRoot = path.join(configRoot, "binaries", "node", "versions");
  if (fs.existsSync(versionsRoot)) {
    for (const version of fs.readdirSync(versionsRoot).sort().reverse()) {
      candidates.push(path.join(versionsRoot, version, process.platform === "win32" ? "node.exe" : "bin/node"));
    }
  }
  for (const candidate of candidates) {
    if (!candidate || !fs.existsSync(candidate)) continue;
    const probe = spawnSync(candidate, ["-e", 'require("node:sqlite")']);
    if (probe.status === 0) return candidate;
  }
  throw new Error("Node.js with node:sqlite is required for the Expert MCP package test");
}

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

test("packaged Expert keeps Report Loop hooks and uses a cross-platform Memory MCP", async () => {
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
      assert.doesNotMatch(command, /\/s|""|\bsh\b/u);
    } else {
      assert.match(command, /^sh "\$\{CODEBUDDY_PLUGIN_ROOT\}\/bin\/run-node" /u);
      assert.match(command, /"\$\{CODEBUDDY_PLUGIN_ROOT\}\/bin\/capture-checkpoint\.mjs"/u);
      assert.doesNotMatch(command, /cmd\.exe|run-node\.cmd|\\bin\\/u);
    }
  }
  const memoryMcp = manifest.mcpServers["report-expert-v2"];
  assert.equal(memoryMcp.command, "node");
  assert.equal(memoryMcp.args[0], "-e");
  assert.match(memoryMcp.args[1], /WORKBUDDY_CONFIG_DIR/u);
  assert.match(memoryMcp.args[1], /CODEBUDDY_CONFIG_DIR/u);
  assert.match(memoryMcp.args[1], /my-experts/u);
  assert.match(memoryMcp.args[1], /report-loop/u);
  assert.match(memoryMcp.args[1], /memory-server\.mjs/u);
  assert.doesNotMatch(JSON.stringify(memoryMcp), /cmd\.exe|run-node\.cmd|"command":"sh"/u);
  assert.deepEqual(memoryMcp.env, {
    RESEARCH_REPORT_MEMORY_V2_0821_DIR: "~/.research-report-memory-v2-0821",
  });

  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-expert-mcp-"));
  const installedExpert = path.join(
    temporary,
    "plugins",
    "marketplaces",
    "my-experts",
    "plugins",
    "report-loop",
  );
  fs.mkdirSync(path.dirname(installedExpert), { recursive: true });
  fs.cpSync(expertDir, installedExpert, { recursive: true });
  const client = new Client({ name: "report-expert-package-test", version: "1" }, { capabilities: {} });
  const managedNode = nodeWithSqlite();
  const transport = new StdioClientTransport({
    command: memoryMcp.command,
    args: memoryMcp.args,
    env: {
      ...process.env,
      ...memoryMcp.env,
      WORKBUDDY_CONFIG_DIR: temporary,
      CODEBUDDY_CONFIG_DIR: temporary,
      CODEBUDDY_PLUGIN_ROOT: path.join(temporary, "stale-plugin-root"),
      CODEBUDDY_NODE_BIN: managedNode,
      PATH: `${path.dirname(managedNode)}${path.delimiter}${process.env.PATH ?? ""}`,
      RESEARCH_REPORT_MEMORY_V2_0821_DIR: path.join(temporary, "memory"),
      RESEARCH_REPORT_MEMORY_SHORTCUT: "0",
    },
    stderr: "pipe",
  });
  try {
    await client.connect(transport);
    const tools = (await client.listTools()).tools.map((tool) => tool.name).sort();
    assert.deepEqual(tools, [
      "report_loop_run",
      "writing_memory_capture_payload",
      "writing_memory_forget",
      "writing_memory_recall",
    ]);
  } finally {
    await client.close();
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
