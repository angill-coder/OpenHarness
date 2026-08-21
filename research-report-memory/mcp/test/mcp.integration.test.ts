import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

function payload(result: Awaited<ReturnType<Client["callTool"]>>): Record<string, any> {
  const first = (result.content as Array<{ type: string; text?: string }>)[0];
  assert.equal(first.type, "text");
  const text = first.text ?? "";
  assert.doesNotMatch(text, /^MCP error/u, text);
  return JSON.parse(text) as Record<string, any>;
}

function serverEntry(projectRoot: string): string | undefined {
  const configured = process.env.RESEARCH_REPORT_MEMORY_SERVER_ENTRY;
  if (!configured) return undefined;
  return path.isAbsolute(configured) ? configured : path.join(projectRoot, configured);
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

test("MVP reviews L0-L3 after feedback and recalls all applicable scopes", async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-v2-mvp-e2e-"));
  const projectRoot = path.resolve(import.meta.dirname, "../..");
  const client = new Client({ name: "research-report-memory-v2-mvp-test", version: "2.0.0" }, { capabilities: {} });
  const transport = new StdioClientTransport({
    command: "sh",
    args: serverEntry(projectRoot)
      ? [path.join(projectRoot, "scripts/run-node.sh"), serverEntry(projectRoot)!]
      : [path.join(projectRoot, "scripts/run-node.sh"), "--import", "tsx", "mcp/src/server.ts"],
    cwd: projectRoot,
    env: { ...process.env, RESEARCH_REPORT_MEMORY_DIR: dataDir } as Record<string, string>,
    stderr: "pipe",
  });

  try {
    await client.connect(transport);
    const tools = await client.listTools();
    assert.deepEqual(tools.tools.map((tool) => tool.name).sort(), [
      "writing_memory_capture", "writing_memory_capture_payload", "writing_memory_forget", "writing_memory_recall",
    ]);

    const invalidPayload = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: "{not-json" },
    }));
    assert.equal(invalidPayload.status, "error");
    assert.match(invalidPayload.reason, /^invalid_capture_payload_json:/u);

    const guessedShape = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "以后数据密集内容都改用表格",
        decision: "store",
        episodes: [{ task: "战略分析报告" }],
        l1Memories: [{ content: "数据密集内容使用表格", scope: "core" }],
      }) },
    }));
    assert.equal(guessedShape.status, "error");
    assert.equal(guessedShape.reason, "invalid_capture_payload_schema");
    assert.ok(guessedShape.issues.some((issue: { code?: string; keys?: string[] }) =>
      issue.code === "unrecognized_keys" && issue.keys?.includes("episodes") && issue.keys?.includes("l1Memories")));

    const missingReview = payload(await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "以后报告始终结论先行",
        decision: "store",
        episode: { task: "战略分析报告" },
        memories: [{ rule: "报告始终结论先行。", scope: "core" }],
      },
    }));
    assert.equal(missingReview.status, "error");
    assert.equal(missingReview.reason, "feedback_review_snapshot_required");

    const pendingReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "DS用户时长分析", audience: "管理委员会", project: "DS用户时长分析", purpose: "review" },
    }));
    const missingEpisode = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "以后数据密集内容都改用表格",
        decision: "store",
        snapshotRevision: pendingReview.snapshotRevision,
        memories: [{ rule: "数据密集内容使用表格。", scope: "core" }],
      }) },
    }));
    assert.equal(missingEpisode.status, "error");
    assert.equal(missingEpisode.reason, "episode_task_missing_in_payload");
    assert.match(missingEpisode.hint, /Runtime creates the Episode/u);

    const missingConversation = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "以后数据密集内容都改用表格",
        decision: "store",
        snapshotRevision: pendingReview.snapshotRevision,
        episode: { task: "DS用户时长分析" },
        memories: [{ rule: "数据密集内容使用表格。", scope: "core" }],
      }) },
    }));
    assert.equal(missingConversation.status, "error");
    assert.equal(missingConversation.reason, "episode_conversation_excerpt_required");

    const pending = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "这份报告控制在三页",
        decision: "pending",
        snapshotRevision: pendingReview.snapshotRevision,
        episode: feedbackEpisode("DS用户时长分析", "这份报告控制在三页", {
          audience: "管理委员会",
          project: "DS用户时长分析",
          contextBefore: "初稿五页",
          contextAfter: "压缩为三页",
          reportBefore: "完整长版报告",
          reportAfter: "三页精简版报告",
        }),
      }) },
    }));
    assert.equal(pending.status, "pending");
    assert.deepEqual(pending.activeRubrics, []);
    const pendingEpisodePath = path.join(dataDir, "l0-l1-memory", "l0-episodes", `${pending.episodeId}.json`);
    assert.ok(fs.existsSync(pendingEpisodePath));
    const pendingEpisode = JSON.parse(fs.readFileSync(pendingEpisodePath, "utf8"));
    assert.equal(pendingEpisode.episodeSchemaVersion, 2);
    assert.deepEqual(pendingEpisode.conversationExcerpt.map((message: any) => message.role), ["assistant", "user"]);
    assert.equal(pendingEpisode.conversationExcerpt[1].content, "这份报告控制在三页");
    assert.equal(pendingEpisode.conversationTruncated, false);

    delete pendingEpisode.conversationExcerpt;
    delete pendingEpisode.conversationSource;
    delete pendingEpisode.conversationTruncated;
    delete pendingEpisode.episodeSchemaVersion;
    fs.writeFileSync(pendingEpisodePath, `${JSON.stringify(pendingEpisode, null, 2)}\n`);
    const enrichedPending = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "这份报告控制在三页",
        decision: "pending",
        snapshotRevision: pendingReview.snapshotRevision,
        episode: feedbackEpisode("DS用户时长分析", "这份报告控制在三页", {
          audience: "管理委员会",
          project: "DS用户时长分析",
          contextBefore: "初稿五页",
          contextAfter: "压缩为三页",
          reportBefore: "完整长版报告",
          reportAfter: "三页精简版报告",
        }),
      }) },
    }));
    assert.equal(enrichedPending.idempotent, true);
    assert.equal(enrichedPending.l0ConversationEnriched, true);
    const enrichedEpisode = JSON.parse(fs.readFileSync(pendingEpisodePath, "utf8"));
    assert.equal(enrichedEpisode.episodeSchemaVersion, 2);
    assert.equal(enrichedEpisode.conversationExcerpt[1].content, "这份报告控制在三页");

    const review = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "战略分析报告", purpose: "review" },
    }));
    assert.equal(review.purpose, "review");

    const core = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "以后报告始终结论先行，不要先铺陈背景",
        decision: "store",
        snapshotRevision: review.snapshotRevision,
        episode: feedbackEpisode("战略分析报告", "以后报告始终结论先行，不要先铺陈背景", { contextBefore: "先写背景", contextAfter: "改为结论先行" }),
        memories: [{
          operationRef: "core-insight",
          rule: "报告开头直接给出核心结论和业务含义。",
          scope: "core",
        }],
        documents: [
          {
            layer: "L2", scope: "core", title: "Writing Core", description: "跨项目写作要求。",
            items: [{ id: "ctx-core-insight-001", summary: "结论先行，优先告诉读者发生了什么。", rules: ["开头直接给出核心结论。"], sourceRefs: ["new:core-insight"] }],
          },
          {
            layer: "L3", scope: "core", title: "Core Rubrics", description: "跨项目自检标准。",
            items: [{
              id: "rubric-core-insight-001", criterion: "报告开头是否直接给出核心结论和业务含义",
              pass: "开头即回答核心问题", fail: "先铺陈背景或研究过程", status: "active", sourceRefs: ["new:core-insight"],
            }],
          },
        ],
      }) },
    }));
    const coreL1 = core.writtenIds[0] as string;
    assert.ok(coreL1);
    assert.equal(core.documentsWritten, 2);
    assert.equal(core.activeRubrics[0].id, "rubric-core-insight-001");

    const missingTargetReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "战略分析报告", purpose: "review" },
    }));
    const missingTarget = payload(await client.callTool({
      name: "writing_memory_capture_payload",
      arguments: { payload: JSON.stringify({
        feedback: "以后报告的核心结论还要更直接",
        decision: "store",
        snapshotRevision: missingTargetReview.snapshotRevision,
        episode: feedbackEpisode("战略分析报告", "以后报告的核心结论还要更直接"),
        memories: [{
          rule: "报告开头必须更直接地给出核心结论。",
          scope: "core",
          action: "update",
        }],
      }) },
    }));
    assert.equal(missingTarget.status, "error");
    assert.equal(missingTarget.reason, "target_ids_required");
    assert.match(missingTarget.hint, /targetIds: \["<existing L1 ID>"\]/u);
    assert.deepEqual(missingTarget.expectedMemoryShape, { action: "update", targetIds: ["m_existing_l1_id"] });

    assert.ok(fs.existsSync(path.join(dataDir, "l2-l3-memory", "personal", "default", ".git")));
    const databasePath = path.join(dataDir, "l0-l1-memory", "memorycore", "vectors.db");
    assert.ok(fs.existsSync(databasePath));
    assert.ok(fs.existsSync(path.join(dataDir, "l0-l1-memory", "l1-atoms", "records")));
    assert.equal(fs.existsSync(path.join(dataDir, "vectors.db")), false);
    assert.equal(fs.existsSync(path.join(dataDir, "episodes")), false);
    const l0Count = execFileSync("sh", [
      path.join(projectRoot, "scripts/run-node.sh"),
      "-e",
      "const {DatabaseSync}=require('node:sqlite');const db=new DatabaseSync(process.argv[1],{readOnly:true});const row=db.prepare('SELECT COUNT(*) AS count FROM l0_conversations').get();db.close();process.stdout.write(String(row.count));",
      databasePath,
    ], { encoding: "utf8" });
    assert.equal(Number(l0Count), 0, "L0 must exist only as complete Episode JSON, not as a SQLite mirror");
    const coreRubrics = fs.readFileSync(path.join(dataDir, "l2-l3-memory", "personal", "default", "system", "l3-rubrics.md"), "utf8");
    assert.match(coreRubrics, /rubric-core-insight-001/u);
    assert.doesNotMatch(coreRubrics, /memory-item/u);

    const noEffectReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "战略分析报告", purpose: "review" },
    }));
    const retrySourceId = "feedback-retry-after-no-effect";
    const noEffect = payload(await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "以后正式报告都要保持结论先行",
        decision: "store",
        snapshotRevision: noEffectReview.snapshotRevision,
        episode: feedbackEpisode("战略分析报告", "以后正式报告都要保持结论先行", { externalSourceId: retrySourceId }),
        documents: [{
          layer: "L2", scope: "core", title: "Writing Core", description: "跨项目写作要求。",
          items: [{
            id: "ctx-core-insight-001", summary: "结论先行，优先告诉读者发生了什么。",
            rules: ["开头直接给出核心结论。"], sourceL1Ids: [coreL1],
          }],
        }],
      },
    }));
    assert.equal(noEffect.status, "error");
    assert.equal(noEffect.reason, "capture_plan_no_effect");
    assert.equal(noEffect.retriable, true);
    const noEffectEpisodePath = path.join(dataDir, "l0-l1-memory", "l0-episodes", `${noEffect.episodeId}.json`);
    assert.equal(JSON.parse(fs.readFileSync(noEffectEpisodePath, "utf8")).status, "pending");

    const correctedRetry = payload(await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "以后正式报告都要保持结论先行",
        decision: "store",
        snapshotRevision: noEffectReview.snapshotRevision,
        episode: feedbackEpisode("战略分析报告", "以后正式报告都要保持结论先行", { externalSourceId: retrySourceId }),
        memories: [{ rule: "正式报告结尾应明确列出可执行行动项。", scope: "core" }],
      },
    }));
    assert.equal(correctedRetry.status, "stored");
    assert.equal(correctedRetry.episodeId, noEffect.episodeId);
    assert.equal(correctedRetry.written, 1);
    assert.equal(JSON.parse(fs.readFileSync(noEffectEpisodePath, "utf8")).status, "promoted");

    const audienceReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "管理层汇报", audience: "管理委员会", purpose: "review" },
    }));
    const audience = payload(await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "以后面向管理委员会的报告，第一页先说北极星指标变化和决策含义",
        decision: "store",
        snapshotRevision: audienceReview.snapshotRevision,
        episode: feedbackEpisode("管理层汇报", "以后面向管理委员会的报告，第一页先说北极星指标变化和决策含义", { audience: "管理委员会" }),
        memories: [{ rule: "面向管理委员会时先说北极星指标变化和决策含义。", scope: "audience", scopeValue: "管理委员会" }],
      },
    }));
    const audienceL1 = audience.writtenIds[0] as string;

    const crossScopeReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "管理层汇报", audience: "管理委员会", purpose: "review" },
    }));
    const crossScope = payload(await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "把管理委员会的北极星指标要求直接作为所有报告的长期规范",
        decision: "store",
        snapshotRevision: crossScopeReview.snapshotRevision,
        episode: feedbackEpisode("管理层汇报", "把管理委员会的北极星指标要求直接作为所有报告的长期规范", { audience: "管理委员会", externalSourceId: "feedback-cross-scope-source" }),
        documents: [{
          layer: "L2", scope: "core", title: "Writing Core", description: "跨项目写作要求。",
          items: [{
            id: "ctx-invalid-cross-scope", summary: "所有报告优先呈现北极星指标。",
            rules: ["第一页先说北极星指标。"], sourceL1Ids: [audienceL1],
          }],
        }],
      },
    }));
    assert.equal(crossScope.status, "error");
    assert.equal(crossScope.reason, `document_source_scope_mismatch:ctx-invalid-cross-scope:${audienceL1}`);

    const audienceSnapshot = payload(await client.callTool({ name: "writing_memory_recall", arguments: { task: "整理", purpose: "maintenance" } }));
    assert.equal("writingEpisodes" in audienceSnapshot, false, "maintenance must not return the full Episode history");
    assert.ok(audienceSnapshot.pendingEpisodes.every((episode: any) => ["pending", "recovery_pending"].includes(episode.status)));
    assert.ok(audienceSnapshot.l1Memories.some((memory: any) => memory.id === audienceL1));
    await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "维护 audience insight", decision: "store", mode: "maintenance", snapshotRevision: audienceSnapshot.snapshotRevision,
        documentPatches: [{
          layer: "L2", scope: "audience", scopeValue: "管理委员会",
          upsertItems: [{ id: "ctx-audience-m-insight-001", summary: "优先呈现北极星指标与决策含义。", rules: ["第一页先说指标变化。"], sourceL1Ids: [audienceL1] }],
        }],
      },
    });

    const projectReview = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "DS用户时长分析", project: "DS用户时长分析", purpose: "review" },
    }));
    const project = payload(await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "以后DS时长项目报告的核心洞察要先区分打开频次与单次时长贡献",
        decision: "store",
        snapshotRevision: projectReview.snapshotRevision,
        episode: feedbackEpisode("DS用户时长分析", "以后DS时长项目报告的核心洞察要先区分打开频次与单次时长贡献", { project: "DS用户时长分析" }),
        memories: [{ rule: "DS时长项目必须区分打开频次和单次时长的贡献。", scope: "project", scopeValue: "DS用户时长分析" }],
      },
    }));
    const projectL1 = project.writtenIds[0] as string;
    const projectSnapshot = payload(await client.callTool({ name: "writing_memory_recall", arguments: { task: "整理", purpose: "maintenance" } }));
    await client.callTool({
      name: "writing_memory_capture",
      arguments: {
        feedback: "维护 project insight", decision: "store", mode: "maintenance", snapshotRevision: projectSnapshot.snapshotRevision,
        documentPatches: [{
          layer: "L2", scope: "project", scopeValue: "DS用户时长分析",
          upsertItems: [{ id: "ctx-project-ds-insight-001", summary: "拆解时长增长来源。", rules: ["区分打开频次和单次时长贡献。"], sourceL1Ids: [projectL1] }],
        }],
      },
    });

    const recalled = payload(await client.callTool({
      name: "writing_memory_recall",
      arguments: { task: "撰写DS时长报告", audience: "管理委员会", project: "DS用户时长分析" },
    }));
    assert.match(recalled.context, /区分打开频次和单次时长/u);
    assert.match(recalled.context, /第一页先说指标变化/u);
    assert.match(recalled.context, /报告开头是否/u);
    assert.ok(recalled.judgeRubrics.some((value: any) => value.id === "rubric-core-insight-001"));

    const forgotten = payload(await client.callTool({ name: "writing_memory_forget", arguments: { id: "rubric-core-insight-001" } }));
    assert.equal(forgotten.deletedHighLevel, 1);
  } finally {
    await client.close();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
