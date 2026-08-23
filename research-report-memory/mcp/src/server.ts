import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { WRITING_MEMORY_SCOPES, WritingMemoryRuntime } from "./runtime.ts";

const server = new McpServer(
  { name: "research-report-memory-v2-mvp", version: "2.0.0-mvp.35" },
  {
    capabilities: { logging: {} },
    instructions: [
      "research-report 专用写作记忆 MVP。",
      "Recall/Capture/Forget 由 research-report-memory-curator WB Sub-agent 调用；主写作 Agent 只有在 Hook 授权后才能使用 Recover 暂存待复核记忆。",
      "L0 Episode 和 L1 Atom 使用 TencentDB MemoryCore；L2 Context 和 L3 Rubrics 使用 Git-backed Markdown Context Repository。",
      "Scope 仅使用 core/audience/project；冲突优先级为本轮要求 > project > audience > core > research-report skill。",
      "每次 writing feedback capture 都即时审视并按需更新 L0-L3；maintenance 只读取待处理/疑似冲突工作集，并以 documentPatches 增量更新 L2/L3。",
    ].join(" "),
  },
);

const runtime = new WritingMemoryRuntime(server);
const scopeSchema = z.enum(WRITING_MEMORY_SCOPES);

function result(payload: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
    structuredContent: payload as Record<string, unknown>,
  };
}

server.registerTool(
  "writing_memory_recall",
  {
    title: "读取报告写作记忆",
    description: "WB Memory Sub-agent 在需求确认后调用；返回 L2 Writing Context、L3 Self-checklist/Judge Rubrics，必要时补充 L1。",
    inputSchema: {
      task: z.string().min(1).describe("已确认的报告任务"),
      audience: z.string().max(160).optional().describe("报告受众或汇报环境"),
      project: z.string().max(200).optional().describe("当前项目名或稳定项目标识"),
      includeL1: z.boolean().optional().default(false).describe("L2/L3 不足、需要特定历史细节时才开启"),
      limit: z.number().int().min(1).max(100).optional(),
      purpose: z.enum(["writing", "judge", "review", "maintenance"]).optional().default("writing"),
    },
  },
  async (input) => result(await runtime.recall(input)),
);

const memorySchema = z.object({
  operationRef: z.string().min(1).max(80).optional().describe("同一次 maintenance 中供高层文档以 new:<ref> 引用"),
  rule: z.string().min(3).max(800).describe("一条可复用的原子写作规则"),
  scope: scopeSchema,
  scopeValue: z.string().min(1).max(200).optional().describe("audience/project scope 必填"),
  sourceEpisodeIds: z.array(z.string().min(1)).max(50).optional(),
  expiresAt: z.string().datetime().optional(),
  action: z.enum(["store", "update", "merge", "skip"]).optional().default("store"),
  targetIds: z.array(z.string().min(1)).max(50).optional()
    .describe("action=update|merge 时必填的原 L1 ID 数组；必须使用复数 targetIds，不能使用 id 或 targetId"),
  lifecycle: z.enum(["candidate", "active"]).optional().default("active"),
});

const sourceFields = {
  sourceL1Ids: z.array(z.string().min(1)).min(1).max(200).optional(),
  sourceRefs: z.array(z.string().min(1)).min(1).max(200).optional().describe("支持 existing:<L1 ID> 或 new:<operationRef>"),
};

const contextDocumentSchema = z.object({
  layer: z.literal("L2"),
  scope: scopeSchema,
  scopeValue: z.string().min(1).max(200).optional(),
  title: z.string().min(1).max(200),
  description: z.string().min(3).max(500),
  items: z.array(z.object({
    id: z.string().min(1).max(120),
    summary: z.string().min(3).max(1500),
    rules: z.array(z.string().min(3).max(800)).min(1).max(30),
    ...sourceFields,
  })).max(100),
});

const rubricDocumentSchema = z.object({
  layer: z.literal("L3"),
  scope: scopeSchema,
  scopeValue: z.string().min(1).max(200).optional(),
  title: z.string().min(1).max(200),
  description: z.string().min(3).max(500),
  items: z.array(z.object({
    id: z.string().min(1).max(120),
    criterion: z.string().min(3).max(1000),
    pass: z.string().min(3).max(1000),
    fail: z.string().min(3).max(1000),
    status: z.enum(["candidate", "active"]),
    ...sourceFields,
  })).max(100),
});

const contextItemSchema = z.object({
  id: z.string().min(1).max(120),
  summary: z.string().min(3).max(1500),
  rules: z.array(z.string().min(3).max(800)).min(1).max(30),
  ...sourceFields,
});

const rubricItemSchema = z.object({
  id: z.string().min(1).max(120),
  criterion: z.string().min(3).max(1000),
  pass: z.string().min(3).max(1000),
  fail: z.string().min(3).max(1000),
  status: z.enum(["candidate", "active"]),
  ...sourceFields,
});

const documentPatchSchema = z.discriminatedUnion("layer", [
  z.object({
    layer: z.literal("L2"), scope: scopeSchema, scopeValue: z.string().min(1).max(200).optional(),
    upsertItems: z.array(contextItemSchema).max(30).optional(),
    removeItemIds: z.array(z.string().min(1).max(120)).max(30).optional(),
  }),
  z.object({
    layer: z.literal("L3"), scope: scopeSchema, scopeValue: z.string().min(1).max(200).optional(),
    upsertItems: z.array(rubricItemSchema).max(30).optional(),
    removeItemIds: z.array(z.string().min(1).max(120)).max(30).optional(),
  }),
]).superRefine((patch, context) => {
  if ((patch.upsertItems?.length ?? 0) === 0 && (patch.removeItemIds?.length ?? 0) === 0) {
    context.addIssue({ code: "custom", message: "document patch requires upsertItems or removeItemIds" });
  }
});

const conversationMessageSchema = z.object({
  role: z.enum(["user", "assistant"]),
  content: z.string().min(1).max(16000),
  messageId: z.string().min(1).max(240).optional(),
  createdAt: z.string().datetime().optional(),
}).strict();

const episodeSchema = z.object({
  task: z.string().min(1).max(800),
  externalSourceId: z.string().min(1).max(500).optional(),
  sessionId: z.string().max(200).optional(),
  topic: z.string().max(200).optional(),
  audience: z.string().max(160).optional(),
  project: z.string().max(200).optional(),
  stage: z.string().max(120).optional(),
  contextBefore: z.string().max(8000).optional(),
  contextAfter: z.string().max(8000).optional(),
  reportBefore: z.string().max(20000).optional(),
  reportAfter: z.string().max(20000).optional(),
  judgeResult: z.string().max(8000).optional(),
  userEdit: z.string().max(8000).optional(),
  finalArtifact: z.string().max(1000).optional(),
  skillVersion: z.string().max(120).optional(),
  rubricsVersion: z.string().max(120).optional(),
  recalledMemoryIds: z.array(z.string().min(1)).max(100).optional(),
  conversationExcerpt: z.array(conversationMessageSchema).min(1).max(8).optional(),
  conversationSource: z.enum(["host_context", "workbuddy_trace_backfill", "manual_backfill"]).optional(),
  conversationTruncated: z.boolean().optional(),
  conversationOmissionReason: z.string().min(1).max(1000).optional(),
}).strict().superRefine((episode, context) => {
  const total = episode.conversationExcerpt?.reduce((sum, message) => sum + message.content.length, 0) ?? 0;
  if (total > 40000) context.addIssue({ code: "custom", path: ["conversationExcerpt"], message: "conversation excerpt must not exceed 40000 characters" });
  if (episode.conversationTruncated && !episode.conversationOmissionReason) {
    context.addIssue({ code: "custom", path: ["conversationOmissionReason"], message: "truncated conversation requires an omission reason" });
  }
});

const captureInputSchema = z.object({
  feedback: z.string().min(1).describe("用户原始反馈，maintenance 时填本次整理说明"),
  decision: z.enum(["store", "pending", "ignore"]),
  mode: z.enum(["feedback", "maintenance", "manage"]).optional().default("feedback"),
  episode: episodeSchema.optional(),
  memories: z.array(memorySchema).max(30).optional(),
  documents: z.array(z.discriminatedUnion("layer", [contextDocumentSchema, rubricDocumentSchema])).max(12).optional(),
  documentPatches: z.array(documentPatchSchema).max(12).optional()
    .describe("增量更新 L2/L3：只提交受影响 Item；upsertItems 按 id 新增或替换，removeItemIds 按 id 删除"),
  snapshotRevision: z.string().min(1).max(128).optional().describe("feedback 模式必须来自 purpose=review；maintenance 模式来自 purpose=maintenance"),
}).strict();

server.registerTool(
  "writing_memory_capture",
  {
    title: "写入与整理报告写作记忆",
    description: "WB Memory Sub-agent 使用。feedback 模式保存 L0/L1 后即时 review 并按需提交 L2/L3；maintenance 模式负责周期性深度治理。",
    inputSchema: captureInputSchema.shape,
  },
  async (input) => result(await runtime.capture(input)),
);

server.registerTool(
  "writing_memory_capture_payload",
  {
    title: "以 JSON Payload 写入报告写作记忆",
    description: "WB Memory Sub-agent 专用稳定 Capture 入口。完整请求编码为一个 JSON 字符串；优先使用 documentPatches 增量更新 L2/L3，避免回写整份文档。",
    inputSchema: {
      payload: z.string().min(2).max(100000).describe("符合 writing_memory_capture schema 的完整 JSON object 字符串"),
    },
  },
  async ({ payload }) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch (error) {
      return result({ status: "error", reason: `invalid_capture_payload_json: ${error instanceof Error ? error.message : String(error)}` });
    }
    const validated = captureInputSchema.safeParse(parsed);
    if (!validated.success) {
      return result({ status: "error", reason: "invalid_capture_payload_schema", issues: validated.error.issues });
    }
    return result(await runtime.capture(validated.data));
  },
);

server.registerTool(
  "writing_memory_forget",
  {
    title: "删除报告写作记忆",
    description: "按 ID 删除 L1/L2/L3，或按文本匹配 L1。只有用户明确要求时才同时删除 L0 Episode。",
    inputSchema: {
      query: z.string().min(1).optional(),
      id: z.string().min(1).optional(),
      includeEpisodes: z.boolean().optional().default(false),
    },
  },
  async (input) => result(await runtime.forget(input)),
);

const shutdown = async () => {
  await runtime.destroy();
  process.exit(0);
};
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);

await runtime.initialize();
await server.connect(new StdioServerTransport());
