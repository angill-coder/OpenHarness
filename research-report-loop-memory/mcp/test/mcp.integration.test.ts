import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

function payload(result: Awaited<ReturnType<Client["callTool"]>>): Record<string, any> {
  const first = (result.content as Array<{ type: string; text?: string }>)[0];
  assert.equal(first.type, "text");
  return JSON.parse(first.text ?? "{}") as Record<string, any>;
}

function feedbackEpisode(task: string, feedback: string, extra: Record<string, unknown> = {}) {
  return {
    task,
    conversationExcerpt: [
      { role: "assistant", content: "上一版报告已经交付，请审阅。" },
      { role: "user", content: feedback },
    ],
    conversationSource: "host_context",
    conversationTruncated: false,
    ...extra,
  };
}

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

test("V2 keeps ordinary feedback in L0/L1 and exposes only explicit L2B rubrics", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-v2-0821-e2e-"));
  const projectRoot = path.resolve(import.meta.dirname, "../..");
  const client = new Client({ name: "report-memory-v2-test", version: "2.1.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    ...serverTransport(projectRoot),
    cwd: projectRoot,
    env: { ...process.env, RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDir } as Record<string, string>,
    stderr: "pipe",
  });

  const feedback = "摘要控制在2–3行，只保留核心观点和推导逻辑";
  const rule = "报告摘要控制在2–3行，只保留核心观点与推导逻辑。";

  try {
    await client.connect(transport);
    const tools = await client.listTools();
    assert.deepEqual(tools.tools.map((tool) => tool.name).sort(), [
      "report_loop_run",
      "writing_memory_capture_payload", "writing_memory_forget", "writing_memory_recall", "writing_memory_settings",
    ]);

    const defaultStatus = payload(await client.callTool({
      name: "writing_memory_settings", arguments: { action: "status" },
    }));
    assert.equal(defaultStatus.memoryEnabled, true);
    const disabled = payload(await client.callTool({
      name: "writing_memory_settings", arguments: { action: "disable" },
    }));
    assert.equal(disabled.memoryEnabled, false);
    const disabledRecall = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "用户研究报告", purpose: "writing" },
    }));
    assert.equal(disabledRecall.reason, "memory_disabled");
    assert.equal(fs.existsSync(path.join(dataDir, "l2b-rubrics")), false);
    const enabled = payload(await client.callTool({
      name: "writing_memory_settings", arguments: { action: "enable" },
    }));
    assert.equal(enabled.memoryEnabled, true);

    const empty = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "用户研究报告", audience: "管理委员会", project: "项目A", purpose: "writing" },
    }));
    assert.equal(empty.status, "ok");
    assert.deepEqual(empty.judgeRubrics, []);
    assert.doesNotMatch(empty.context, /writing-context|L1 Atom/u);

    const review1 = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "用户研究报告", query: feedback, purpose: "review", includeL1: true },
    }));
    assert.equal(review1.baseRubricIndex, undefined, "Memory Agent must not pre-map Base checks");
    assert.equal(review1.rubricSetVersion, "v0");
    const first = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: review1.snapshotRevision,
        episode: feedbackEpisode("用户研究报告", feedback, { externalSourceId: "session-1:feedback-1", sessionId: "session-1", project: "项目A" }),
        atoms: [{ operationRef: "summary-concise", rule, scope: "core", lifecycle: "candidate" }],
      }) },
    }));
    assert.equal(first.status, "stored");
    assert.equal(first.written, 1);
    assert.equal(first.rubricsWritten, 0, "a normal first feedback must not update L2B");
    const atomId = first.writtenIds[0] as string;
    assert.ok(atomId);

    const episodePath = path.join(dataDir, "l0-l1-memory", "l0-episodes", `${first.episodeId}.json`);
    const episode = JSON.parse(fs.readFileSync(episodePath, "utf8"));
    assert.equal(episode.status, "processed");
    assert.equal(episode.conversationExcerpt.at(-1).content, feedback);
    assert.deepEqual(episode.linkedL1Ids, [atomId]);

    const review2 = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "另一份研究报告", query: feedback, purpose: "review", includeL1: true },
    }));
    assert.equal(review2.l1Memories[0].evidence.sourceCount, 1);
    const second = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: review2.snapshotRevision,
        episode: feedbackEpisode("另一份研究报告", feedback, { externalSourceId: "session-2:feedback-1", sessionId: "session-2", project: "项目B" }),
        atoms: [{ rule, scope: "core", lifecycle: "candidate" }],
      }) },
    }));
    assert.equal(second.status, "stored");
    assert.equal(second.writtenIds[0], atomId, "exact corroboration should reuse the L1 atom");
    assert.equal(second.rubricsWritten, 0, "Runtime gathers evidence but never auto-promotes without Curator judgement");

    const review3 = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "第三份研究报告", query: feedback, purpose: "review", includeL1: true },
    }));
    const evidence = review3.l1Memories.find((value: any) => value.id === atomId).evidence;
    assert.equal(evidence.sourceCount, 2);
    assert.equal(evidence.independentSessions, 2);

    const promoteFeedback = "这是我长期坚持的写作要求，以后所有正式报告都按这个标准检查";
    const promoted = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: promoteFeedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: review3.snapshotRevision,
        episode: feedbackEpisode("第三份研究报告", promoteFeedback, { externalSourceId: "session-3:feedback-1", sessionId: "session-3", project: "项目C" }),
        atoms: [{ operationRef: "summary-long-term", rule, scope: "core", lifecycle: "candidate" }],
        rubricPatches: [{
          scope: "core",
          upsertItems: [{
            id: "MR-EXPRESSION-SUMMARY-CONCISE",
            statement: "摘要控制在2–3行，仅呈现核心观点及其关键推导逻辑，不展开过程信息。",
            status: "active",
            sourceRefs: ["new:summary-long-term"],
          }],
        }],
      }) },
    }));
    assert.equal(promoted.status, "stored");
    assert.equal(promoted.rubricsWritten, 1);
    assert.equal(promoted.rubricSetVersion, "v1");

    const recalled = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "新报告", audience: "管理委员会", project: "项目D", purpose: "writing" },
    }));
    assert.equal(recalled.judgeRubrics.length, 1);
    assert.equal(recalled.judgeRubrics[0].id, "MR-EXPRESSION-SUMMARY-CONCISE");
    assert.equal(recalled.memoryRubrics.length, 1);
    assert.match(recalled.context, /\[core\] 摘要控制在2–3行/u);
    assert.deepEqual(recalled.sources.l1, [], "writing recall must not expose L1 by default");

    const rubricPath = path.join(dataDir, "l2b-rubrics", "personal", "default", "system", "rubrics.json");
    assert.ok(fs.existsSync(rubricPath));
    const storedRubrics = JSON.parse(fs.readFileSync(rubricPath, "utf8"));
    assert.equal(storedRubrics.schemaVersion, 3);
    assert.equal(storedRubrics.rubrics[0].statement, recalled.memoryRubrics[0].statement);
    assert.equal(fs.existsSync(path.join(dataDir, "l2-l3-memory")), false);

    const invalid = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: promoteFeedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: await currentRevision(client),
        episode: feedbackEpisode("新报告", promoteFeedback, { externalSourceId: "session-4:feedback-1" }),
        rubricPatches: [{
          scope: "core",
          upsertItems: [{
            id: "MR-INVALID-REDLINE", criterionKey: "expression.invalid_redline", operation: "add", dimension: "expression", label: "非法红线",
            desc: "不允许 Memory 创建红线。", effect: "测试。", redline: true, status: "active", sourceL1Ids: [atomId],
          }],
        }],
      }) },
    }));
    assert.equal(invalid.status, "error");
    assert.equal(invalid.reason, "invalid_capture_payload_schema");

    const forgotten = payload(await client.callTool({
      name: "writing_memory_forget",
      arguments: { id: "MR-EXPRESSION-SUMMARY-CONCISE" },
    }));
    assert.equal(forgotten.deletedHighLevel, 1);
  } finally {
    await client.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

async function currentRevision(client: Client): Promise<string> {
  const review = payload(await client.callTool({
    name: "writing_memory_recall",
    arguments: { task: "schema check", purpose: "review", includeL1: true },
  }));
  return review.snapshotRevision as string;
}

test("Memory Agent Context stores report background without creating L1 or L2B", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-agent-context-"));
  fs.writeFileSync(path.join(dataDir, "settings.json"), JSON.stringify({ schemaVersion: 1, memoryEnabled: true }));
  const projectRoot = path.resolve(import.meta.dirname, "../..");
  const client = new Client({ name: "research-report-memory-agent-context-test", version: "2.1.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    ...serverTransport(projectRoot),
    cwd: projectRoot,
    env: { ...process.env, RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDir } as Record<string, string>,
    stderr: "pipe",
  });

  try {
    await client.connect(transport);
    const feedback = "A 和决策委员会 A 都是指同一位决策者。";
    const review = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "战略研究报告", query: feedback, purpose: "review", includeL1: true },
    }));
    assert.match(review.agentContext, /# Memory Agent Context/u);

    const agentContextDocument = review.agentContext.replace(
      "## Audiences\n",
      "## Audiences\n- A、决策委员会 A：均指同一位决策者。\n",
    );
    const stored = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: review.snapshotRevision,
        episode: feedbackEpisode("战略研究报告", feedback, {
          externalSourceId: "agent-context:audience-alias",
          sessionId: "agent-context-session",
        }),
        agentContextDocument,
      }) },
    }));
    assert.equal(stored.status, "stored");
    assert.equal(stored.written, 0);
    assert.equal(stored.rubricsWritten, 0);
    assert.equal(stored.agentContextUpdated, true);

    const next = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "另一份报告", query: "决策委员会 A", purpose: "review", includeL1: true },
    }));
    assert.match(next.agentContext, /A、决策委员会 A：均指同一位决策者/u);
    assert.deepEqual(next.l1Memories, []);
    assert.deepEqual(next.rubricDocuments.flatMap((document: any) => document.rubrics), []);
    assert.equal(
      fs.readFileSync(path.join(dataDir, "agent-context.md"), "utf8"),
      agentContextDocument,
    );

    const reflection = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { purpose: "reflection", includeL1: true },
    }));
    assert.equal(reflection.agentContext, agentContextDocument);
  } finally {
    await client.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("project Atom inherits a missing scopeValue only from its Episode", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-scope-fallback-"));
  fs.writeFileSync(path.join(dataDir, "settings.json"), JSON.stringify({ schemaVersion: 1, memoryEnabled: true }));
  const projectRoot = path.resolve(import.meta.dirname, "../..");
  const client = new Client({ name: "research-report-memory-scope-fallback-test", version: "2.1.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    ...serverTransport(projectRoot),
    cwd: projectRoot,
    env: { ...process.env, RESEARCH_REPORT_MEMORY_V2_0821_DIR: dataDir } as Record<string, string>,
    stderr: "pipe",
  });

  try {
    await client.connect(transport);
    const feedback = "本项目统一使用行为数据口径";
    const review = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "项目报告", project: "项目回填测试", query: feedback, purpose: "review", includeL1: true },
    }));
    const stored = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback,
        decision: "store",
        mode: "feedback",
        snapshotRevision: review.snapshotRevision,
        episode: feedbackEpisode("项目报告", feedback, {
          externalSourceId: "scope-fallback:project",
          sessionId: "scope-fallback",
          project: "项目回填测试",
        }),
        atoms: [{ rule: "本项目统一使用行为数据口径。", scope: "project", lifecycle: "candidate" }],
      }) },
    }));
    assert.equal(stored.status, "stored");
    assert.equal(stored.written, 1);

    const recalled = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "项目报告", project: "项目回填测试", query: feedback, purpose: "review", includeL1: true },
    }));
    assert.equal(recalled.l1Memories[0].scope, "project");
    assert.equal(recalled.l1Memories[0].scopeValue, "项目回填测试");

    const missingReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "另一个项目报告", query: "项目规则", purpose: "review", includeL1: true },
    }));
    const rejected = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "这份项目报告必须统一使用行为数据口径",
        decision: "store",
        mode: "feedback",
        snapshotRevision: missingReview.snapshotRevision,
        episode: feedbackEpisode("另一个项目报告", "这份项目报告必须统一使用行为数据口径", {
          externalSourceId: "scope-fallback:missing",
          sessionId: "scope-fallback-missing",
        }),
        atoms: [{ rule: "本项目报告固定使用行为数据口径。", scope: "project", lifecycle: "candidate" }],
      }) },
    }));
    assert.equal(rejected.status, "error");
    assert.equal(rejected.reason, "scope_value_required");
  } finally {
    await client.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
