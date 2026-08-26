import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

function serverTransport(projectRoot: string) {
  const configured = process.env.RESEARCH_REPORT_MEMORY_SERVER_ENTRY?.trim();
  const launcher = path.join(projectRoot, "scripts", process.platform === "win32" ? "run-node.cmd" : "run-node.sh");
  const entry = configured
    ? path.isAbsolute(configured) ? configured : path.join(projectRoot, configured)
    : undefined;
  const args = entry ? [entry] : ["--import", "tsx", "mcp/src/server.ts"];
  return process.platform === "win32"
    ? { command: "cmd.exe", args: ["/d", "/c", launcher, ...args] }
    : { command: "sh", args: [launcher, ...args] };
}

test("unified MCP launches the packaged Python Runner outside the Agent contract", async () => {
  const projectRoot = path.resolve(import.meta.dirname, "../..");
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-mcp-launcher-"));
  const jobPath = path.join(temporary, "invalid-job.json");
  fs.writeFileSync(jobPath, JSON.stringify({ schemaVersion: 1 }));
  const client = new Client({ name: "report-loop-launcher-test", version: "1" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    ...serverTransport(projectRoot),
    cwd: projectRoot,
    env: {
      ...process.env,
      RESEARCH_REPORT_MEMORY_V2_0821_DIR: path.join(temporary, "memory"),
      RESEARCH_REPORT_MEMORY_SHORTCUT: "0",
    } as Record<string, string>,
    stderr: "pipe",
  });
  try {
    await client.connect(transport);
    const response = await client.callTool({ name: "report_loop_run", arguments: { jobPath } });
    const text = (response.content as Array<{ type: string; text?: string }>)[0]?.text ?? "{}";
    const payload = JSON.parse(text);
    assert.equal(payload.status, "error");
    assert.equal(payload.reason, "schemaVersion must be 2");
  } finally {
    await client.close();
    fs.rmSync(temporary, { recursive: true, force: true });
  }
});
