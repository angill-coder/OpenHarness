import readline from "node:readline";
import fs from "node:fs/promises";
import path from "node:path";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WritingMemoryRuntime } from "../mcp/src/runtime.ts";

type Request = { id?: string; method?: string; params?: Record<string, unknown> };

const runtime = new WritingMemoryRuntime({} as McpServer);
await runtime.initialize();

async function dispatch(request: Request) {
  const params = request.params ?? {};
  switch (request.method) {
    case "handshake":
      return {
        runtime: "research-report-memory-v2-mvp",
        version: "1.0.3",
        schemaVersion: 2,
        capabilities: ["l0", "l1", "l2", "store", "update", "merge", "skip", "snapshot_revision", "source_refs"],
      };
    case "policy":
      return {
        instructions: await fs.readFile(
          path.resolve("agents/research-report-memory-curator.md"),
          "utf8",
        ),
      };
    case "recall":
      return runtime.recall(params as never);
    case "maintenance_snapshot":
      return runtime.recall({
        task: String(params.task ?? "OpenHarness feedback memory maintenance"),
        purpose: "maintenance",
        limit: Number(params.limit ?? 100),
      });
    case "capture":
      return runtime.capture(params as never);
    case "forget":
      return runtime.forget(params as never);
    default:
      throw new Error(`unsupported_method:${String(request.method ?? "")}`);
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  if (!line.trim()) continue;
  let request: Request = {};
  try {
    request = JSON.parse(line) as Request;
    const result = await dispatch(request);
    process.stdout.write(`${JSON.stringify({ id: request.id, ok: true, result })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      id: request.id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    })}\n`);
  }
}

await runtime.destroy();
