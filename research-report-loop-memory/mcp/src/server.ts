import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { autoEnableReflection } from "./reflection-auto-enable.ts";
import { ReportLoopLauncher } from "./report-loop-launcher.ts";
import { WRITING_MEMORY_SCOPES, WritingMemoryRuntime } from "./runtime.ts";

const server = new McpServer(
  { name: "report-memory-v2", version: "1.0.0-mvp.40" },
  {
    capabilities: { logging: {} },
    instructions: [
      "report-memory-v2：research-report 专用写作记忆服务。",
      "实时 Recall/Capture/Forget 由 research-report-memory-curator WB Sub-agent 调用；后台 consolidation 由 research-report-memory-reflection WB Sub-agent 调用。主写作 Agent 只有在 Hook 授权后才能使用 Recover 暂存待复核记忆。",
      "L0 Episode 和 L1 Atom 使用 TencentDB MemoryCore；L2B 是独立的 Git-backed Memory Rubrics，不改写 Base Rubrics。",
      "Scope 仅使用 core/audience/project；冲突优先级为本轮要求 > project > audience > core > research-report skill。",
      "每次 writing feedback capture 都保存 L0、按需聚合 L1，并保守判断是否更新 L2B；普通单次反馈默认不改变 L2B。",
      "report_loop_run 是宿主侧薄 Launcher：只在 MCP 宿主边界启动现有 Python Runner，不承载 Report Loop 业务逻辑。",
    ].join(" "),
  },
);

const runtime = new WritingMemoryRuntime(server);
const reportLoopLauncher = new ReportLoopLauncher();
const scopeSchema = z.enum(WRITING_MEMORY_SCOPES);

function result(payload: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(payload, null, 2) }],
    structuredContent: payload as Record<string, unknown>,
  };
}

server.registerTool(
  "report_loop_run",
  {
    title: "启动报告评测改写循环",
    description: "宿主侧薄 Launcher。传入绝对 Job JSON 路径，在 Agent 沙箱外运行现有 Python Report Loop Runner，并返回 Runner 的最终 JSON。",
    inputSchema: {
      jobPath: z.string().min(1).max(4000).describe("宿主 Agent 已写入的 Job Schema v2 JSON 绝对路径"),
    },
  },
  async ({ jobPath }) => result(await reportLoopLauncher.run(jobPath)),
);

server.registerTool(
  "writing_memory_recall",
  {
    title: "读取报告写作记忆",
    description: "WB Memory Sub-agent 用于 review/reflection；Report Loop 直接读取并解析版本化 Rubric Set。",
    inputSchema: {
      task: z.string().min(1).optional().describe("已确认的报告任务；reflection 时省略"),
      query: z.string().max(2000).optional().describe("review 时传当前反馈，用于选择相关历史证据"),
      audience: z.string().max(160).optional().describe("报告受众或汇报环境"),
      project: z.string().max(200).optional().describe("当前项目名或稳定项目标识"),
      includeL1: z.boolean().optional().default(false).describe("仅在 Memory Agent 需要原子证据时开启；写作 Recall 默认关闭"),
      limit: z.number().int().min(1).max(100).optional(),
      purpose: z.enum(["writing", "judge", "review", "reflection"]).optional().default("writing"),
    },
  },
  async (input) => result(await runtime.recall(input)),
);

const memorySchema = z.object({
  operationRef: z.string().min(1).max(80).optional().describe("同一次 capture 中供高层文档以 new:<ref> 引用"),
  rule: z.string().min(3).max(800).describe("一条可复用的原子写作规则"),
  scope: scopeSchema,
  scopeValue: z.string().min(1).max(200).optional().describe("audience/project scope 必填"),
  sourceEpisodeIds: z.array(z.string().min(1)).max(50).optional(),
  expiresAt: z.string().datetime().optional(),
  action: z.enum(["store", "update", "merge", "skip"]).optional().default("store"),
  targetIds: z.array(z.string().min(1)).max(50).optional()
    .describe("action=update|merge 时必填的原 L1 ID 数组；必须使用复数 targetIds，不能使用 id 或 targetId"),
  lifecycle: z.enum(["candidate", "promoted", "superseded"]).optional().default("candidate"),
});

const sourceFields = {
  sourceL1Ids: z.array(z.string().min(1)).min(1).max(200).optional(),
  sourceRefs: z.array(z.string().min(1)).min(1).max(200).optional().describe("支持 existing:<L1 ID> 或 new:<operationRef>"),
};

const rubricItemSchema = z.object({
  id: z.string().min(1).max(120),
  statement: z.string().min(3).max(1600).describe("可独立评判、但不预先绑定 Base 维度或 Check 的用户写作标准"),
  status: z.literal("active").default("active"),
  ...sourceFields,
}).strict();

const rubricPatchSchema = z.object({
  scope: scopeSchema,
  scopeValue: z.string().min(1).max(200).optional(),
  upsertItems: z.array(rubricItemSchema).max(30).optional(),
  removeItemIds: z.array(z.string().min(1).max(120)).max(30).optional(),
}).superRefine((patch, context) => {
  if ((patch.upsertItems?.length ?? 0) === 0 && (patch.removeItemIds?.length ?? 0) === 0) {
    context.addIssue({ code: "custom", message: "rubric patch requires upsertItems or removeItemIds" });
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
  feedback: z.string().min(1).describe("用户原始反馈；reflection 时填本次审视说明"),
  decision: z.enum(["store", "pending", "ignore"]),
  mode: z.enum(["feedback", "reflection", "manage"]).optional().default("feedback"),
  episode: episodeSchema.optional(),
  atoms: z.array(memorySchema).max(30).optional(),
  rubricPatches: z.array(rubricPatchSchema).optional()
    .describe("增量升级 Rubric Set Overlay；普通反馈证据不足时省略此字段"),
  agentContextDocument: z.string().min(25).max(20000).optional()
    .describe("Memory Agent 自用的完整 Markdown 上下文；仅在用户、受众或项目背景发生明确变化时传入，不属于 Rubrics"),
  snapshotRevision: z.string().min(1).max(128).optional().describe("feedback 模式来自 purpose=review；reflection 模式来自 purpose=reflection"),
  reflectionThrough: z.string().datetime().optional().describe("reflection 模式必须原样带回 Snapshot 的增量截止时间"),
}).strict();

server.registerTool(
  "writing_memory_capture_payload",
  {
    title: "以 JSON Payload 写入报告写作记忆",
    description: "WB Memory Sub-agent 唯一 Capture 入口。feedback 传完整反馈和 Episode；reflection 只需 mode、snapshotRevision、reflectionThrough 及增量 changes，服务端自动补齐固定字段。",
    inputSchema: {
      payload: z.string().min(2).max(100000).describe("Capture JSON object 字符串。reflection 可省略 feedback 和 decision；服务端按 reflection/store 处理"),
    },
  },
  async ({ payload }) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch (error) {
      return result({ status: "error", reason: `invalid_capture_payload_json: ${error instanceof Error ? error.message : String(error)}` });
    }
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const value = parsed as Record<string, unknown>;
      if (value.mode === "reflection") {
        parsed = {
          feedback: "Scheduled memory reflection",
          decision: "store",
          ...value,
        };
      }
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
    description: "按 ID 删除 L1/L2B，或按文本匹配 L1。只有用户明确要求时才同时删除 L0 Episode。",
    inputSchema: {
      query: z.string().min(1).optional(),
      id: z.string().min(1).optional(),
      includeEpisodes: z.boolean().optional().default(false),
    },
  },
  async (input) => result(await runtime.forget(input)),
);

const shutdown = async () => {
  await reportLoopLauncher.destroy();
  await runtime.destroy();
  process.exit(0);
};
process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);

await runtime.initialize();
await server.connect(new StdioServerTransport());
autoEnableReflection(import.meta.url);
