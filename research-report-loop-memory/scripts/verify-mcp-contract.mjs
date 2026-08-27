import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const expected = [
  "report_loop_run",
  "writing_memory_capture_payload",
  "writing_memory_forget",
  "writing_memory_recall",
  "writing_memory_settings",
];

async function listTools(command, args, env) {
  return await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: pluginRoot,
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const settleAfterClose = (callback) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.once("close", callback);
      child.kill();
    };
    const timer = setTimeout(() => {
      settleAfterClose(() => reject(new Error(`Memory MCP tools/list timed out: ${stderr.trim()}`)));
    }, 20000);
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      const lines = stdout.split(/\r?\n/u);
      stdout = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        let message;
        try { message = JSON.parse(line); } catch { continue; }
        if (message.id !== 2) continue;
        settleAfterClose(() => {
          if (message.error) reject(new Error(JSON.stringify(message.error)));
          else resolve((message.result?.tools ?? []).map((tool) => tool.name).sort());
        });
      }
    });
    child.once("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "package-preflight", version: "1" } } })}\n`);
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} })}\n`);
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} })}\n`);
  });
}

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-memory-preflight-"));
try {
  const memoryTools = await listTools(
    process.execPath,
    [path.join(pluginRoot, "dist", "memory-server.mjs")],
    {
      RESEARCH_REPORT_MEMORY_V2_0821_DIR: path.join(temporary, "memory"),
      RESEARCH_REPORT_MEMORY_SHORTCUT: "0",
      RESEARCH_REPORT_BASE_RUBRIC_PATH: path.join(pluginRoot, "rubrics", "v2_rubric_research.json"),
    },
  );
  if (JSON.stringify(memoryTools) !== JSON.stringify(expected)) {
    throw new Error(`Memory MCP tool contract mismatch: ${JSON.stringify(memoryTools)}`);
  }
  process.stdout.write(`${JSON.stringify({ status: "ok", platform: process.platform, memoryTools }, null, 2)}\n`);
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
