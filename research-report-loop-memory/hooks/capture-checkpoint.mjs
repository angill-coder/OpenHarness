import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { classifyWritingFeedback } from "../mcp/src/relevance.ts";

const MEMORY_AGENT = "research-report-memory-curator";
const MEMORY_AGENT_MARKER = /\bMEMORY_CAPTURE_COMPLETED\b/iu;
const MEMORY_AGENT_FAILURE_MARKER = /MEMORY_CAPTURE_FAILED(?:\s+reason=|:)/iu;
const AGENT_TOOL_PATTERN = /^(?:agent|task|subagent|spawn_agent)$/iu;
const SKILL_TOOL_PATTERN = /^(?:skill|use_skill|skillmanage)$/iu;
const SKILL_REF_PATTERN = /research-report-loop(?!-memory)(?![\w-])/iu;
const REPORT_TASK_PATTERN =
  /(?:写|撰写|生成|制作|改写|修改|润色|完善|分析|输出).{0,16}(?:报告|汇报|文稿|稿件|材料|文章|摘要)|(?:报告|汇报|文稿|稿件|材料|文章|摘要).{0,16}(?:写|撰写|生成|制作|改写|修改|润色|完善|分析|输出)|战略分析|研究报告|高管汇报|复盘报告|storyline/iu;
const REVISION_FEEDBACK_PATTERN =
  /(?:这份|这版|上一版|上版|刚才|刚刚|现有|当前(?:版本|稿件|报告)|初稿|终稿|你(?:刚才)?写的|上面(?:的)?).{0,40}(?:报告|汇报|正文|摘要|章节|段落|标题|内容|写法|表达)?|(?:正文|摘要|章节|段落|标题|表格|bullet).{0,24}(?:太|不够|过于|改成|改为|删掉|删除|去掉|保留|调整|压缩|展开|补充|缺少)|(?:太|不够|过于).{0,24}(?:简洁|冗长|啰嗦|口语|正式|完整|清晰|直接|深入|浅)/iu;
const LONG_TERM_PREFERENCE_PATTERN = /以后|下次|长期|始终|所有报告|每份报告|都这样写|固定写法/iu;
const MEMORY_MANAGEMENT_PATTERN =
  /(?:记忆|memory).{0,24}(?:查看|列出|纠错|修正|改成|改为|调整|归类|分类|scope|合并|删除|忘记|清除)|(?:查看|列出|纠错|修正|改成|改为|调整|归类|分类|scope|合并|删除|忘记|清除).{0,24}(?:记忆|memory)/iu;
const NEW_REPORT_PATTERN = /另一份|再写|重新写|新(?:的)?报告|换.{0,8}(?:报告|汇报|主题)/iu;
const CANCEL_REPORT_PATTERN = /(?:不写了|不用写了|取消(?:这次|本次)?(?:报告|任务)?|先暂停|停止(?:写作|任务)?|算了)/iu;

function emptyState(sessionId) {
  return {
    version: 1,
    sessionId,
    active: false,
    reportProduced: false,
    capturePending: false,
    pendingFeedback: "",
    updatedAt: new Date().toISOString(),
  };
}

function safeStringify(value) {
  try {
    return typeof value === "string" ? value : JSON.stringify(value) ?? "";
  } catch {
    return "";
  }
}

function successfulToolResult(result) {
  if (result === undefined || result === null) return true;
  return !/"isError"\s*:\s*true|"status"\s*:\s*"error"|tool[_ ]error|execution failed/iu.test(
    safeStringify(result),
  );
}

function parsePayload(value) {
  if (value === undefined || value === null) return {};
  if (Array.isArray(value)) {
    for (const item of value) {
      const nested = parsePayload(item);
      if (Object.keys(nested).length > 0) return nested;
    }
    return {};
  }
  if (typeof value === "object") {
    if (value.structuredContent && typeof value.structuredContent === "object") return value.structuredContent;
    if (typeof value.status === "string") return value;
    for (const key of ["text", "content", "output", "toolResult"]) {
      if (!(key in value)) continue;
      const nested = parsePayload(value[key]);
      if (Object.keys(nested).length > 0) return nested;
    }
    return {};
  }
  if (typeof value !== "string" || !value.trim()) return {};
  try {
    return parsePayload(JSON.parse(value));
  } catch {
    const match = value.match(/\{[\s\S]*\}/u);
    if (!match) return {};
    try {
      return parsePayload(JSON.parse(match[0]));
    } catch {
      return {};
    }
  }
}

function resolvedToolName(toolName, toolInput) {
  if (toolName !== "DeferExecuteTool" || !toolInput || typeof toolInput !== "object") return toolName;
  for (const key of ["toolName", "tool_name", "name"]) {
    if (typeof toolInput[key] === "string" && toolInput[key].trim()) return toolInput[key].trim();
  }
  return toolName;
}

function isMemoryAgentInvocation(toolName, toolInput) {
  return AGENT_TOOL_PATTERN.test(toolName) && safeStringify(toolInput).includes(MEMORY_AGENT);
}

function detectSkillActivation(toolName, toolInput) {
  if (!SKILL_TOOL_PATTERN.test(toolName)) return false;
  const fields = [toolInput?.skill, toolInput?.command, toolInput?.name];
  return SKILL_REF_PATTERN.test(fields.filter((value) => typeof value === "string").join(" "));
}

function resolveStateDir() {
  const configured = process.env.RESEARCH_REPORT_CAPTURE_HOOK_DIR?.trim();
  return (configured || path.join(os.homedir(), ".research-report-memory-v2-0821", "capture-hook-state"))
    .replace(/^~(?=$|\/)/u, os.homedir());
}

function statePath(sessionId) {
  const key = crypto.createHash("sha256").update(sessionId).digest("hex");
  return path.join(resolveStateDir(), `${key}.json`);
}

async function readInput() {
  let raw = "";
  for await (const chunk of process.stdin) raw += chunk;
  if (!raw.trim()) return {};
  const value = JSON.parse(raw);
  return value && typeof value === "object" ? value : {};
}

function requireSessionId(input) {
  const value = typeof input.session_id === "string" ? input.session_id.trim() : "";
  if (!value) throw new Error("missing session_id");
  return value;
}

async function loadState(sessionId) {
  try {
    const parsed = JSON.parse(await fs.readFile(statePath(sessionId), "utf8"));
    return parsed?.version === 1 ? { ...emptyState(sessionId), ...parsed, sessionId } : emptyState(sessionId);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    return emptyState(sessionId);
  }
}

async function saveState(state) {
  const file = statePath(state.sessionId);
  await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify({ ...state, updatedAt: new Date().toISOString() }, null, 2)}\n`, { mode: 0o600 });
  await fs.rename(temporary, file);
}

function printJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

async function onPrompt(input) {
  const state = await loadState(requireSessionId(input));
  const prompt = typeof input.prompt === "string" ? input.prompt.trim()
    : typeof input.user_prompt === "string" ? input.user_prompt.trim() : "";

  if (CANCEL_REPORT_PATTERN.test(prompt)) Object.assign(state, emptyState(state.sessionId));

  const classified = classifyWritingFeedback(prompt);
  const reportTask = REPORT_TASK_PATTERN.test(prompt) && !NEW_REPORT_PATTERN.test(prompt);
  const revisionFeedback = classified.relevant && REVISION_FEEDBACK_PATTERN.test(prompt)
    && !NEW_REPORT_PATTERN.test(prompt);
  const longTermPreference = classified.relevant && LONG_TERM_PREFERENCE_PATTERN.test(prompt);
  const memoryManagement = MEMORY_MANAGEMENT_PATTERN.test(prompt) && !revisionFeedback;

  if (reportTask) state.active = true;
  if (!reportTask && !memoryManagement && classified.relevant
    && (state.reportProduced || revisionFeedback || (state.active && longTermPreference))) {
    state.active = true;
    state.capturePending = true;
    state.pendingFeedback = classified.writingText;
  }

  await saveState(state);
  const systemMessage = state.capturePending
    ? [
      `检测到用户对报告写法的反馈：${state.pendingFeedback}。`,
      "先落实当前报告修改，再通过 Agent/Task 委派 research-report-memory-curator 执行 operation=capture。",
      "这是独立的 research-report-memory-v2-0821（L0 Writing Episode / L1 Atom / L2B Rubrics），不是 WorkBuddy MEMORY.md 或项目日志。",
      "不要由主 Agent 直接调用 Memory MCP，也不要把 WorkBuddy 原生 Memory 当作替代写入。",
    ].join(" ")
    : "";
  printJson({ continue: true, suppressOutput: true, ...(systemMessage ? { systemMessage } : {}) });
}

async function onPostTool(input) {
  const state = await loadState(requireSessionId(input));
  const toolName = typeof input.tool_name === "string" ? input.tool_name : "";
  const toolInput = input.tool_input && typeof input.tool_input === "object" ? input.tool_input : {};
  const actualToolName = resolvedToolName(toolName, toolInput);
  const result = input.tool_response !== undefined ? input.tool_response : input.tool_result;
  const successful = successfulToolResult(result);
  let message = "";

  if (successful && detectSkillActivation(toolName, toolInput)) state.active = true;
  if (successful && actualToolName.endsWith("report_loop_finish")) {
    const payload = parsePayload(result);
    if (payload.status === "completed") {
      state.active = true;
      state.reportProduced = true;
    }
  }

  if (isMemoryAgentInvocation(toolName, toolInput) && state.capturePending) {
    const serialized = safeStringify(result);
    if (MEMORY_AGENT_MARKER.test(serialized)) {
      state.capturePending = false;
      state.pendingFeedback = "";
      message = "Report Memory Capture 已完成。";
    } else if (MEMORY_AGENT_FAILURE_MARKER.test(serialized) || !successful) {
      state.capturePending = false;
      state.pendingFeedback = "";
      message = "Report Memory Capture 明确失败；允许结束，但必须如实说明未写入专用记忆。";
    }
  }

  await saveState(state);
  printJson({ continue: true, suppressOutput: true, ...(message ? { systemMessage: message } : {}) });
}

async function onStop(input) {
  const state = await loadState(requireSessionId(input));
  if (!state.capturePending) {
    printJson({ hookSpecificOutput: { hookEventName: "Stop", permissionDecision: "allow" } });
    return;
  }
  if (input.stop_hook_active === true) {
    printJson({
      continue: true,
      suppressOutput: true,
      systemMessage: "Capture checkpoint 已避免重复阻断；pending 状态保留到下一轮。",
    });
    return;
  }
  const reason = [
    `research-report 专用 Memory Capture 尚未完成：${state.pendingFeedback || "用户本轮写作反馈"}。`,
    `仅委派 ${MEMORY_AGENT} 执行 operation=capture，取得 MEMORY_CAPTURE_COMPLETED 或明确失败后结束。`,
    "不要重复报告修改、Report Loop 或正文输出，不要写 WorkBuddy MEMORY.md 代替。",
  ].join("");
  printJson({
    hookSpecificOutput: {
      hookEventName: "Stop",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
    reason,
    systemMessage: "Report Memory Capture checkpoint 已阻止本轮遗漏。",
  });
}

async function main() {
  const mode = process.argv[2];
  const input = await readInput();
  if (mode === "prompt") return onPrompt(input);
  if (mode === "post-tool") return onPostTool(input);
  if (mode === "stop") return onStop(input);
  throw new Error(`unsupported hook mode: ${mode || "<empty>"}`);
}

main().catch((error) => {
  process.stderr.write(`[research-report-loop-memory] capture hook failed: ${error?.message || error}\n`);
  process.exitCode = 1;
});
