import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

function payload(result: Awaited<ReturnType<Client["callTool"]>>): Record<string, any> {
  const first = (result.content as Array<{ type: string; text?: string }>)[0];
  return JSON.parse(first.text ?? "{}") as Record<string, any>;
}

test("Reflection reviews processed episodes once and accepts an unchanged checkpoint", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-reflection-"));
  const root = path.resolve(import.meta.dirname, "../..");
  const client = new Client({ name: "reflection-test", version: "1.0.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    command: "sh",
    args: [path.join(root, "scripts/run-node.sh"), "--import", "tsx", "mcp/src/server.ts"],
    cwd: root,
    env: { ...process.env, RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDir } as Record<string, string>,
    stderr: "pipe",
  });

  try {
    await client.connect(transport);
    const feedback = "正式报告摘要控制在2–3行，只保留核心观点。";
    const review = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "测试报告", query: feedback, purpose: "review", includeL1: true },
    }));
    const captured = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: review.snapshotRevision,
        episode: {
          task: "测试报告",
          externalSourceId: "reflection-session:feedback-1",
          sessionId: "reflection-session",
          conversationExcerpt: [
            { role: "assistant", content: "这是报告初稿。" },
            { role: "user", content: feedback },
          ],
          conversationSource: "host_context",
          conversationTruncated: false,
        },
        atoms: [{ rule: "正式报告摘要控制在2–3行，只保留核心观点。", scope: "core", lifecycle: "candidate" }],
      }) },
    }));
    assert.equal(captured.status, "stored");

    const reflection = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { purpose: "reflection", includeL1: true },
    }));
    assert.equal(reflection.status, "ok");
    assert.equal(reflection.purpose, "reflection");
    assert.equal(reflection.noWork, false, "processed feedback must still be visible to Reflection");
    assert.equal(reflection.changedEpisodes.length, 1);
    assert.equal(reflection.l1Memories.length, 1);
    assert.ok(reflection.snapshotRevision);
    assert.ok(reflection.reflectionThrough);

    const acknowledged = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        mode: "reflection",
        atoms: [],
        rubricPatches: [],
        snapshotRevision: reflection.snapshotRevision,
        reflectionThrough: reflection.reflectionThrough,
      }) },
    }));
    assert.equal(acknowledged.status, "unchanged");
    assert.equal(acknowledged.stored, true);

    const next = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { purpose: "reflection", includeL1: true },
    }));
    assert.equal(next.noWork, true);
    assert.deepEqual(next.changedEpisodes, []);
    assert.equal(fs.existsSync(path.join(dataDir, "reflection", "state.json")), true);
  } finally {
    await client.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("Reflection uses one compact Capture to merge L1 evidence and update L2B", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-reflection-compact-"));
  const root = path.resolve(import.meta.dirname, "../..");
  const client = new Client({ name: "reflection-compact-test", version: "1.0.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    command: "sh",
    args: [path.join(root, "scripts/run-node.sh"), "--import", "tsx", "mcp/src/server.ts"],
    cwd: root,
    env: { ...process.env, RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDir } as Record<string, string>,
    stderr: "pipe",
  });

  try {
    await client.connect(transport);
    const firstFeedback = "报告不要出现面向作者的防御性表述。";
    const firstReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "报告A", query: firstFeedback, purpose: "review", includeL1: true },
    }));
    const first = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: firstFeedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: firstReview.snapshotRevision,
        episode: {
          task: "报告A", externalSourceId: "reflection-compact:first", sessionId: "session-a",
          conversationExcerpt: [{ role: "assistant", content: "这是初稿。" }, { role: "user", content: firstFeedback }],
          conversationSource: "host_context", conversationTruncated: false,
        },
        atoms: [{ rule: "终稿不使用面向作者的防御性表述。", scope: "core" }],
      }) },
    }));
    const oldAtomId = first.writtenIds[0] as string;

    const secondFeedback = "另一份报告也不要写样本有限、不可外推等自辩。";
    const secondReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "报告B", query: secondFeedback, purpose: "review", includeL1: true },
    }));
    const second = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: secondFeedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: secondReview.snapshotRevision,
        episode: {
          task: "报告B", externalSourceId: "reflection-compact:second", sessionId: "session-b",
          conversationExcerpt: [{ role: "assistant", content: "这是另一份初稿。" }, { role: "user", content: secondFeedback }],
          conversationSource: "host_context", conversationTruncated: false,
        },
        atoms: [{ rule: "终稿不使用样本有限、不可外推等自辩。", scope: "core" }],
      }) },
    }));

    const reflection = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { purpose: "reflection", includeL1: true },
    }));
    const compact = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        mode: "reflection",
        snapshotRevision: reflection.snapshotRevision,
        reflectionThrough: reflection.reflectionThrough,
        atoms: [{
          rule: "终稿不使用面向作者的防御性、过程稿或自辩式表述。",
          scope: "core",
          action: "merge",
          targetIds: [oldAtomId],
          sourceEpisodeIds: [second.episodeId],
        }],
        rubricPatches: [{
          scope: "core",
          upsertItems: [{
            id: "MR-NO-DEFENSIVE",
            statement: "检查终稿是否出现面向作者的防御性、过程稿或自辩式表述。",
            sourceL1Ids: [oldAtomId],
          }],
        }],
      }) },
    }));
    assert.equal(compact.status, "stored");
    assert.equal(compact.written, 1);
    assert.equal(compact.rubricsWritten, 1);
    const mergedAtomId = compact.writtenIds[0] as string;
    assert.notEqual(mergedAtomId, oldAtomId);

    const recalled = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "报告C", query: "防御性表述", purpose: "review", includeL1: true },
    }));
    const merged = recalled.l1Memories.find((item: any) => item.id === mergedAtomId);
    assert.equal(merged.evidence.sourceCount, 2, "merge must inherit old Episode evidence and add the new source");
    assert.deepEqual(recalled.rubricDocuments[0].rubrics[0].sourceL1Ids, [mergedAtomId]);
  } finally {
    await client.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
