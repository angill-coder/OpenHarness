import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { classifyWritingFeedback } from "../mcp/src/relevance.ts";

const REPORT_TASK_PATTERN =
  /(?:写|撰写|生成|制作|改写|修改|润色|完善|分析|输出).{0,16}(?:报告|汇报|文稿|稿件|材料|文章|摘要)|(?:报告|汇报|文稿|稿件|材料|文章|摘要).{0,16}(?:写|撰写|生成|制作|改写|修改|润色|完善|分析|输出)|战略分析|研究报告|高管汇报|复盘报告|storyline/iu;
const NEW_REPORT_PATTERN = /另一份|再写|重新写|新(?:的)?报告|换.{0,8}(?:报告|汇报|主题)/iu;
// Revision feedback must not be mistaken for a brand-new report task. Keep
// this conservative: require either a reference to an existing draft or an
// evaluative/editing signal tied to a report section. Explicit new-report
// language always wins over this pattern.
const REVISION_FEEDBACK_PATTERN =
  /(?:这份|这版|上一版|上版|刚才|刚刚|现有|当前(?:版本|稿件|报告)|初稿|终稿|你(?:刚才)?写的|上面(?:的)?).{0,40}(?:报告|汇报|正文|摘要|章节|段落|标题|内容|写法|表达)?|(?:正文|摘要|章节|段落|标题|表格|bullet).{0,24}(?:太|不够|过于|改成|改为|删掉|删除|去掉|保留|调整|压缩|展开|补充|缺少)|(?:太|不够|过于).{0,24}(?:简洁|冗长|啰嗦|口语|正式|完整|清晰|直接|深入|浅)/iu;
const MEMORY_MANAGEMENT_PATTERN =
  /(?:记忆|memory).{0,24}(?:查看|列出|纠错|修正|改成|改为|调整|归类|分类|scope|合并|删除|忘记|清除)|(?:查看|列出|纠错|修正|改成|改为|调整|归类|分类|scope|合并|删除|忘记|清除).{0,24}(?:记忆|memory)/iu;
// Meta-operations (building/configuring/packaging the expert or plugin itself)
// must NOT be treated as a research-report writing task, otherwise every
// "create/pack/rename/configure the expert" request falsely activates the
// guard and demands a write-before recall. Genuine report requests do not
// contain these tooling-construction phrases.
const META_OPERATION_PATTERN = /(?:打包|改名|调试|export|创建(?:一个)?(?:专家|expert|插件)|生成(?:一个)?(?:专家|expert|插件)|修改(?:这个)?专家|编辑(?:这个)?专家|改专家|专家改|专家包|插件包|把.{0,30}打包成.{0,10}(?:专家|插件)|把插件|插件改|创建插件|生成插件)/iu;
const RECALL_TOOL = "writing_memory_recall";
const CAPTURE_TOOL = "writing_memory_capture";
const MEMORY_AGENT = "research-report-memory-curator";
const MEMORY_RECALL_MARKER = /MEMORY_RECALL_COMPLETED/iu;
const MEMORY_RECALL_FAILURE_MARKER = /MEMORY_RECALL_FAILED\s+reason=/iu;
const MEMORY_AGENT_MARKER = /MEMORY_CAPTURE_COMPLETED\s+status=(?:stored|pending|unchanged|ignored)/iu;
const MEMORY_AGENT_FAILURE_MARKER = /MEMORY_CAPTURE_FAILED\s+reason=/iu;
const AGENT_TOOL_PATTERN = /^(?:agent|task|subagent|spawn_agent)$/iu;
const INTAKE_TOOL_PATTERN = /^(?:AskUserQuestion|ask_user_question)$/iu;
const CANCEL_REPORT_PATTERN = /(?:不写了|不用写了|取消(?:这次|本次)?(?:报告|任务)?|先暂停|停止(?:写作|任务)?|算了)/iu;

// Primary activation signal: the research-report skill itself being loaded.
// Prompt-text matching is only a supplementary fast path — it cannot be relied
// upon, because users phrase report requests in ways no regex fully covers.
const SKILL_NAME = "research-report";
const SKILL_TOOL_PATTERN = /^(?:skill|use_skill|skillmanage)$/iu;
// Matches the skill's own files, e.g. skills/research-report/SKILL.md and
// skills/research-report/references/instructions.md. Deliberately anchored on
// the `skills/` segment so unrelated plugin sources do not trigger activation.
const SKILL_FILE_PATTERN = /(?:^|\/)skills\/research-report\/(?:SKILL\.md|references\/)/iu;
// `research-report-memory` embeds the skill name, so require the reference to
// end at a boundary rather than continue into the plugin name.
const SKILL_REF_PATTERN = /research-report(?!-memory)(?![\w-])/iu;

function detectSkillActivation(toolName, toolInput) {
  if (!toolName) return false;

  if (SKILL_TOOL_PATTERN.test(toolName)) {
    // Only the explicit skill-reference fields are considered here. We
    // deliberately do NOT fall back to scanning the whole serialized tool_input:
    // many hosts inject the full available-skills catalog (which includes the
    // `research-report` entry) into the skill tool's input, and the old pattern
    // matched that entry and falsely activated the guard on unrelated skill
    // loads (e.g. loading `expert-manager` triggered it). A missed activation is
    // fail-open -- only a softer recall reminder is lost -- whereas a false
    // activation is fail-closed: it blocks every Write/Edit. We therefore err
    // toward open. Prompt-path activation (REPORT_TASK_PATTERN) remains as the
    // supplementary fast path, and SKILL_FILE_PATTERN still covers file loads.
    // Match ONLY the explicit skill-identifier fields (skill/command/name). We
    // intentionally drop `args` here: some hosts serialize the full
    // available-skills catalog (which contains a `research-report` entry) into
    // `args`, and matching it there re-introduced the false-positive activation
    // even after the safeStringify fallback was removed.
    const fields = [toolInput?.skill, toolInput?.command, toolInput?.name];
    const joined = fields.filter((value) => typeof value === "string").join(" ");
    return SKILL_REF_PATTERN.test(joined);
  }

  const filePath = typeof toolInput?.file_path === "string" ? toolInput.file_path : "";
  return SKILL_FILE_PATTERN.test(filePath);
}

function safeStringify(value) {
  try {
    return typeof value === "string" ? value : JSON.stringify(value) ?? "";
  } catch {
    return "";
  }
}

function isMemoryAgentInvocation(toolName, toolInput) {
  if (!AGENT_TOOL_PATTERN.test(toolName)) return false;
  return safeStringify(toolInput).includes(MEMORY_AGENT);
}

function resolveStateDir() {
  const explicit = process.env.RESEARCH_REPORT_MEMORY_GUARD_DIR?.trim();
  if (explicit) return explicit.replace(/^~(?=$|\/)/u, os.homedir());
  const memoryRoot = process.env.RESEARCH_REPORT_MEMORY_V2_DIR?.trim()
    || process.env.RESEARCH_REPORT_MEMORY_DIR?.trim();
  const root = memoryRoot
    ? memoryRoot.replace(/^~(?=$|\/)/u, os.homedir())
    : path.join(os.homedir(), ".research-report-memory-v2-mvp");
  return path.join(root, "hook-state");
}

function statePath(sessionId) {
  const key = crypto.createHash("sha256").update(sessionId).digest("hex");
  return path.join(resolveStateDir(), `${key}.json`);
}

function emptyState(sessionId) {
  return {
    version: 8,
    sessionId,
    researchReportActive: false,
    skillActivated: false,
    activationSource: "",
    recallPending: false,
    recallDueNow: false,
    capturePending: false,
    revisionFeedbackPending: false,
    pendingFeedback: "",
    updatedAt: new Date().toISOString(),
  };
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

// WorkBuddy sends UserPromptSubmit as { prompt }. Older/other hosts use
// { user_prompt }. Read both so a host field rename cannot silently disable
// the guard again.
function readPrompt(input) {
  const candidates = [input.prompt, input.user_prompt];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

// WorkBuddy sends PostToolUse as { tool_response }; accept { tool_result } too.
function readToolResult(input) {
  return input.tool_response !== undefined ? input.tool_response : input.tool_result;
}

async function loadState(sessionId) {
  try {
    const parsed = JSON.parse(await fs.readFile(statePath(sessionId), "utf8"));
    return { ...emptyState(sessionId), ...parsed, sessionId };
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
    return emptyState(sessionId);
  }
}

async function saveState(state) {
  const file = statePath(state.sessionId);
  await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const next = { ...state, updatedAt: new Date().toISOString() };
  const temporary = `${file}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(next, null, 2)}\n`, { mode: 0o600 });
  await fs.rename(temporary, file);
}

function printJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function successfulToolResult(result) {
  if (result === undefined || result === null) return true;
  const serialized = typeof result === "string" ? result : JSON.stringify(result);
  return !/"isError"\s*:\s*true|"status"\s*:\s*"error"|tool[_ ]error|execution failed/iu.test(
    serialized,
  );
}

function activateSkill(state, source) {
  const firstActivation = !state.researchReportActive;
  state.researchReportActive = true;
  state.skillActivated = true;
  if (!state.activationSource) state.activationSource = source;
  // Only arm the recall gate on first activation, so a mid-session skill reload
  // does not re-block a session that already completed recall. A fresh task
  // that is explicitly revising an existing report goes straight to capture.
  if (firstActivation && !state.revisionFeedbackPending) {
    state.recallPending = true;
    state.recallDueNow = false;
  }
  return firstActivation;
}

async function onPrompt(input) {
  const sessionId = requireSessionId(input);
  const prompt = readPrompt(input);
  const state = await loadState(sessionId);
  const wasAwaitingRecall = state.researchReportActive && state.recallPending;
  const classifiedFeedback = classifyWritingFeedback(prompt);
  const revisionFeedback = classifiedFeedback.relevant
    && REVISION_FEEDBACK_PATTERN.test(prompt)
    && !NEW_REPORT_PATTERN.test(prompt);
  // A report can itself discuss memory architecture. Revision language such as
  // "这份报告里的 memory 分类改成..." remains writing feedback; only otherwise
  // explicit requests to manage stored Memory enter the administration path.
  const explicitMemoryManagement = MEMORY_MANAGEMENT_PATTERN.test(prompt) && !revisionFeedback;
  const feedback = explicitMemoryManagement
    ? { relevant: false, reason: "explicit_memory_management", writingText: "" }
    : classifiedFeedback;

  if (state.researchReportActive && CANCEL_REPORT_PATTERN.test(prompt)) {
    state.researchReportActive = false;
    state.recallPending = false;
    state.recallDueNow = false;
    state.capturePending = false;
    state.revisionFeedbackPending = false;
    state.pendingFeedback = "";
  }

  // Supplementary fast path: obvious report requests arm the gate before the
  // skill even loads. Never the sole activation route.
  if (!CANCEL_REPORT_PATTERN.test(prompt) && !explicitMemoryManagement
    && REPORT_TASK_PATTERN.test(prompt) && !META_OPERATION_PATTERN.test(prompt)) {
    if (!state.researchReportActive) {
      if (revisionFeedback) {
        state.revisionFeedbackPending = true;
        activateSkill(state, "revision-feedback");
      } else {
        activateSkill(state, "prompt");
      }
    } else if (NEW_REPORT_PATTERN.test(prompt)) {
      state.revisionFeedbackPending = false;
      state.recallPending = true;
      state.recallDueNow = false;
    }
  }

  // A new user turn while intake is still open is a clarification response.
  // The model may ask for another missing field, but it must not end this turn
  // with a progress-only message: ask via AskUserQuestion or complete recall.
  if (wasAwaitingRecall && state.researchReportActive && state.recallPending
    && prompt && !CANCEL_REPORT_PATTERN.test(prompt)) {
    state.recallDueNow = true;
  }

  // Always classify feedback, even before activation. The skill often loads
  // later in the same turn, and the feedback must not be lost in that window.
  if (feedback.relevant) {
    state.capturePending = true;
    if (revisionFeedback) state.revisionFeedbackPending = true;
    state.pendingFeedback = feedback.writingText;
  }

  await saveState(state);

  const reminders = [];
  if (state.researchReportActive && state.recallPending) {
    reminders.push(
      state.recallDueNow
        ? `这是 research-report 写作任务，且已收到需求补充。本轮不得只回复“准备召回”后结束：若三项输入齐全，立即通过 Agent/Task 委派 ${MEMORY_AGENT} 执行 recall；若仍缺信息，只用 AskUserQuestion 补齐，并在工具返回后于同一轮完成委派。必须收到 MEMORY_RECALL_COMPLETED；不要由主 Agent 直接调用 ${RECALL_TOOL}。`
        : `这是 research-report 写作任务。先完成 Skill 第0步的需求澄清；三项输入确认齐全后、首次写入报告前委派 ${MEMORY_AGENT} 执行 recall。必须收到 MEMORY_RECALL_COMPLETED，然后在同一轮直接进入素材分析和写作；不要由主 Agent 直接调用 ${RECALL_TOOL}，不要重复提问。`,
    );
  }
  if (state.researchReportActive && state.capturePending) {
    reminders.push(
      `本轮包含可能需要写作记忆处理的反馈：${state.pendingFeedback}。先完成当前修改，再委派 ${MEMORY_AGENT} 独立判断 ignore/pending/store 并调用 ${CAPTURE_TOOL}；不要在主写作上下文里展开记忆整理。之后才能交付或总结。`,
      "本轮必须使用 operation=capture；即使反馈与旧 Memory 冲突，也不得改走 manage 或在 Capture 期间单独调用 forget。",
    );
  }
  printJson({
    continue: true,
    suppressOutput: true,
    ...(reminders.length > 0 ? { systemMessage: reminders.join("\n") } : {}),
  });
}

async function onPreTool(input) {
  const sessionId = requireSessionId(input);
  const state = await loadState(sessionId);
  const toolName = typeof input.tool_name === "string" ? input.tool_name : "";

  if (!state.researchReportActive) {
    printJson({ continue: true, suppressOutput: true });
    return;
  }

  let reason = "";
  if (state.recallPending && /^(?:Write|Edit|MultiEdit|NotebookEdit|ApplyPatch|apply_patch)$/u.test(toolName)) {
    reason = `即将开始写入报告，但尚未完成写作记忆读取。请先委派 ${MEMORY_AGENT}；收到 MEMORY_RECALL_COMPLETED 或明确的 MEMORY_RECALL_FAILED 后，再重试本次写入。`;
  } else if (state.capturePending && /^(?:present_files)$/iu.test(toolName)) {
    reason = `当前报告修改已完成，但写作反馈尚未经过 Memory checkpoint。请先委派 ${MEMORY_AGENT}；收到 MEMORY_CAPTURE_COMPLETED 或明确的 MEMORY_CAPTURE_FAILED 后，再交付。`;
  }

  if (!reason) {
    printJson({ continue: true, suppressOutput: true });
    return;
  }

  printJson({
    // permissionDecisionReason is what the host surfaces to the model; without
    // it the denial arrives with no actionable explanation.
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
    systemMessage: reason,
  });
}

async function onPostTool(input) {
  const sessionId = requireSessionId(input);
  const state = await loadState(sessionId);
  const toolName = typeof input.tool_name === "string" ? input.tool_name : "";
  const toolResult = readToolResult(input);
  const serializedResult = safeStringify(toolResult);
  const memoryAgentInvocation = isMemoryAgentInvocation(toolName, input.tool_input);
  const successful = successfulToolResult(toolResult);

  let changed = false;
  let activationMessage = "";

  // Primary activation: the research-report skill was loaded in this session.
  if (successful && detectSkillActivation(toolName, input.tool_input)) {
    const firstActivation = activateSkill(state, "skill");
    changed = true;
    if (firstActivation && state.recallPending) {
      activationMessage =
        `已检测到 research-report skill 加载。先完成开场三项需求澄清；确认齐全后、首次写入报告前委派 ${MEMORY_AGENT} 执行 recall，收到 MEMORY_RECALL_COMPLETED 后直接继续写作。`;
    } else if (firstActivation && state.revisionFeedbackPending) {
      activationMessage =
        `已检测到对现有报告的修改反馈。本轮不重新触发写前 recall；先完成修改，再以 operation=capture 委派 ${MEMORY_AGENT}，不得改走 manage 或单独调用 forget。`;
    }
  }

  if (successful && INTAKE_TOOL_PATTERN.test(toolName)
    && state.researchReportActive && state.recallPending) {
    state.recallDueNow = true;
    changed = true;
    activationMessage =
      `第0步需求回答已返回。本轮不得停在“准备 recall”的说明上：三项齐全时立即委派 ${MEMORY_AGENT}；如仍缺一项，只补问缺项，并在回答返回后于同一轮完成委派。`;
  }

  if (memoryAgentInvocation && MEMORY_RECALL_MARKER.test(serializedResult)) {
    state.researchReportActive = true;
    state.skillActivated = true;
    if (!state.activationSource) state.activationSource = "memory-subagent-recall";
    state.recallPending = false;
    state.recallDueNow = false;
    changed = true;
  }
  if (memoryAgentInvocation && state.recallPending
    && (MEMORY_RECALL_FAILURE_MARKER.test(serializedResult) || !successful)) {
    state.recallPending = false;
    state.recallDueNow = false;
    changed = true;
    activationMessage = "Memory Recall 明确失败；本轮按无个性化记忆继续写作，并向用户如实说明。不得把失败描述成‘没有匹配记忆’。";
  }
  if (memoryAgentInvocation && MEMORY_AGENT_MARKER.test(serializedResult)) {
    state.capturePending = false;
    state.revisionFeedbackPending = false;
    state.pendingFeedback = "";
    changed = true;
  }
  if (memoryAgentInvocation && state.capturePending
    && (MEMORY_AGENT_FAILURE_MARKER.test(serializedResult) || !successful)) {
    state.capturePending = false;
    state.revisionFeedbackPending = false;
    state.pendingFeedback = "";
    changed = true;
    activationMessage = "Memory Capture 明确失败；本轮允许继续交付，但必须向用户如实说明反馈未写入记忆。不要由主 Agent补写或宣称已保存。";
  }
  if (changed) await saveState(state);

  printJson({
    continue: true,
    suppressOutput: true,
    ...(activationMessage ? { systemMessage: activationMessage } : {}),
  });
}

async function onStop(input) {
  const sessionId = requireSessionId(input);
  const state = await loadState(sessionId);

  // The host sets stop_hook_active when this Stop already ran as a result of a
  // previous Stop block. Blocking again would loop forever, so give up the gate
  // and let the turn finish; the pending flags stay for the next turn.
  if (input.stop_hook_active === true) {
    printJson({
      continue: true,
      suppressOutput: true,
      systemMessage:
        "Memory Guard 检测到重复阻断，已放行本轮以避免死循环。请仍然补齐缺失的写作记忆调用。",
    });
    return;
  }

  const missing = [];
  // A clarification turn must be allowed to end while recall is pending.
  // Recall remains a hard gate at the first Write/Edit via PreToolUse. Capture
  // becomes enforceable only after recall marks the transition into drafting.
  if (state.researchReportActive && state.recallPending && state.recallDueNow) {
    missing.push(
      `需求补充已经返回，但尚未完成写前召回。不要只说明“准备召回”；若三项齐全，立即通过 Agent/Task 委派 ${MEMORY_AGENT} 并取得 MEMORY_RECALL_COMPLETED；若仍有缺项，只用 AskUserQuestion 补齐，并在工具返回后于同一轮完成委派`,
    );
  }
  if (state.researchReportActive && !state.recallPending && state.capturePending) {
    missing.push(
      `委派 ${MEMORY_AGENT} 处理本轮写作反馈并调用 ${CAPTURE_TOOL}（可用 ignore/pending/store）：${state.pendingFeedback || "用户本轮写作反馈"}`,
    );
  }

  if (missing.length === 0) {
    // hookSpecificOutput.permissionDecision replaces the deprecated
    // `decision` field, which makes the host log a deprecation warning.
    printJson({
      hookSpecificOutput: {
        hookEventName: "Stop",
        permissionDecision: "allow",
      },
    });
    return;
  }

  // The host injects this text as a `role:"user"` message before re-invoking the
  // model, so it reads as a fresh user turn. Without an explicit scope limit the
  // model tends to regenerate its whole previous answer. State the missing call
  // and forbid redoing completed work.
  const reason = [
    `research-report Memory checkpoint 未完成：${missing.join("；")}。`,
    "仅补齐上述调用，然后直接结束本轮。",
    "不要重复本轮已经完成的分析、提问或正文输出，也不要重新生成已交付的文件。",
  ].join("");

  printJson({
    hookSpecificOutput: {
      hookEventName: "Stop",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
    reason,
    systemMessage: "Memory Guard 已阻止本轮结束；请仅补齐缺失的 Memory Sub-agent 委派。",
  });
}

async function main() {
  if (process.env.RESEARCH_REPORT_MEMORY_GUARD === "off") {
    printJson({ continue: true, suppressOutput: true });
    return;
  }
  const mode = process.argv[2];
  const input = await readInput();
  if (mode === "prompt") return onPrompt(input);
  if (mode === "pre-tool") return onPreTool(input);
  if (mode === "post-tool") return onPostTool(input);
  if (mode === "stop") return onStop(input);
  throw new Error(`unsupported hook mode: ${mode || "<empty>"}`);
}

main().catch((error) => {
  // Fail open on hook implementation errors so the plugin cannot deadlock WorkBuddy.
  process.stderr.write(`[research-report-memory-v2-mvp] hook failed: ${error?.message || error}\n`);
  process.exitCode = 1;
});
