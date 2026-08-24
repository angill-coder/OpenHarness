import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const expected = {
  "research-report-loop": [
    "report_loop_finish",
    "report_loop_start",
    "report_loop_status",
    "report_loop_submit",
  ],
  "research-report-memory-v2-0821": [
    "writing_memory_capture",
    "writing_memory_capture_payload",
    "writing_memory_forget",
    "writing_memory_recall",
  ],
};

function walkExecutables(root, names) {
  if (!root || !fs.existsSync(root)) return [];
  const matches = [];
  const pending = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const value = path.join(current, entry.name);
      if (entry.isDirectory()) pending.push(value);
      else if (names.includes(entry.name.toLowerCase())) matches.push(value);
    }
  }
  return matches.sort().reverse();
}

function usable(command, args = ["--version"]) {
  if (!command) return false;
  const result = spawnSync(command, args, { encoding: "utf8", timeout: 5000 });
  return result.status === 0;
}

function findPython() {
  const explicit = process.env.WORKBUDDY_PYTHON;
  if (usable(explicit)) return explicit;
  const root = path.join(os.homedir(), ".workbuddy", "binaries", "python", "versions");
  for (const candidate of walkExecutables(root, ["python.exe", "python3"])) {
    if (usable(candidate)) return candidate;
  }
  for (const candidate of process.platform === "win32" ? ["python.exe", "python"] : ["python3", "python"]) {
    if (usable(candidate)) return candidate;
  }
  throw new Error("Python 3.10+ not found; set WORKBUDDY_PYTHON before verification.");
}

async function listTools(name, command, args, env) {
  return await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: pluginRoot,
      env: { ...process.env, ...env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`${name} tools/list timed out: ${stderr.trim()}`));
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
        clearTimeout(timer);
        child.kill();
        if (message.error) reject(new Error(`${name}: ${JSON.stringify(message.error)}`));
        else resolve((message.result?.tools ?? []).map((tool) => tool.name).sort());
      }
    });
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "package-preflight", version: "1" } } })}\n`);
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} })}\n`);
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} })}\n`);
  });
}

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "report-loop-memory-preflight-"));
try {
  const python = findPython();
  const reportTools = await listTools(
    "research-report-loop",
    python,
    [path.join(pluginRoot, "mcp", "report_loop", "server.py")],
    { RESEARCH_REPORT_LOOP_DIR: path.join(temporary, "report-loop") },
  );
  const memoryTools = await listTools(
    "research-report-memory-v2-0821",
    process.execPath,
    [path.join(pluginRoot, "dist", "memory-server.mjs")],
    {
      RESEARCH_REPORT_MEMORY_V2_0821_DIR: path.join(temporary, "memory"),
      RESEARCH_REPORT_MEMORY_SHORTCUT: "0",
      RESEARCH_REPORT_BASE_RUBRIC_PATH: path.join(pluginRoot, "rubrics", "v2_rubric_research.json"),
    },
  );
  for (const [name, actual] of Object.entries({
    "research-report-loop": reportTools,
    "research-report-memory-v2-0821": memoryTools,
  })) {
    if (JSON.stringify(actual) !== JSON.stringify(expected[name])) {
      throw new Error(`${name} tool contract mismatch: ${JSON.stringify(actual)}`);
    }
  }
  process.stdout.write(`${JSON.stringify({ status: "ok", platform: process.platform, reportTools, memoryTools }, null, 2)}\n`);
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
