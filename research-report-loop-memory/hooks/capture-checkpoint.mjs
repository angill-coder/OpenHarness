import crypto from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { classifyWritingFeedback } from "../mcp/src/relevance.ts";
import { ReportLoopLauncher } from "../mcp/src/report-loop-launcher.ts";

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
const REPORT_CONTEXT_PATTERN =
  /(?:是指|指的是|都是指|都指|别名|简称|全称|身份是|汇报对象是)|(?:我是|用户是|我在|用户在|我来自|用户来自).{0,36}(?:分析师|研究员|产品经理|咨询顾问|公司|团队|部门)|(?:本项目|这个项目|当前项目|项目背景|项目口径|统一口径|项目术语)/iu;
const MEMORY_MANAGEMENT_PATTERN =
  /(?:记忆|memory).{0,24}(?:查看|列出|纠错|修正|改成|改为|调整|归类|分类|scope|合并|删除|忘记|清除)|(?:查看|列出|纠错|修正|改成|改为|调整|归类|分类|scope|合并|删除|忘记|清除).{0,24}(?:记忆|memory)/iu;
const NEW_REPORT_PATTERN = /另一份|再写|重新写|新(?:的)?报告|换.{0,8}(?:报告|汇报|主题)/iu;
const CANCEL_REPORT_PATTERN = /(?:不写了|不用写了|取消(?:这次|本次)?(?:报告|任务)?|先暂停|停止(?:写作|任务)?|算了)/iu;
const WRITE_TOOL_PATTERN = /^(?:write|edit|create_file)$/iu;
const REPORT_LOOP_SIDECAR_SUFFIX = ".session.json";
const REPORT_LOOP_LOCK_MAX_AGE_MS = 2 * 60 * 60 * 1000;
const REPORT_LOOP_WAIT_TIMEOUT_MS = 65 * 60 * 1000;
const REPORT_LOOP_WAIT_INTERVAL_MS = 1000;

function emptyState(sessionId) {
  return {
    version: 1,
    sessionId,
    active: false,
    reportProduced: false,
    capturePending: false,
    pendingFeedback: "",
    pendingKind: "writing",
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

async function writeJsonAtomic(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await fs.rename(temporary, file);
}

async function readJson(file) {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return undefined;
    throw error;
  }
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'"'"'`)}'`;
}

function reportLoopWaitCommand(resultPath, statusPath) {
  const scriptPath = fileURLToPath(import.meta.url);
  return [
    process.execPath,
    ...process.execArgv,
    scriptPath,
    "wait-loop-result",
    resultPath,
    statusPath,
  ].map(shellQuote).join(" ");
}

function reportLoopArtifactPaths(jobPath, jobPayload) {
  const digest = crypto
    .createHash("sha256")
    .update(JSON.stringify(jobPayload))
    .digest("hex")
    .slice(0, 12);
  const prefix = `${jobPath}.report-loop.${digest}`;
  return {
    digest,
    lockPath: `${prefix}.lock`,
    statusPath: `${prefix}.status.json`,
    resultPath: `${prefix}.result.json`,
  };
}

async function acquireLaunchLock(lockPath) {
  try {
    const handle = await fs.open(lockPath, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() })}\n`);
    await handle.close();
    return true;
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }
  try {
    const stat = await fs.stat(lockPath);
    if (Date.now() - stat.mtimeMs <= REPORT_LOOP_LOCK_MAX_AGE_MS) return false;
    await fs.unlink(lockPath);
    return acquireLaunchLock(lockPath);
  } catch (error) {
    if (error?.code === "ENOENT") return acquireLaunchLock(lockPath);
    throw error;
  }
}

async function launchReportLoopHostWorker(jobPath, paths) {
  const existingResult = await readJson(paths.resultPath);
  if (existingResult) return { ...paths, state: "completed", launched: false };
  const locked = await acquireLaunchLock(paths.lockPath);
  if (!locked) return { ...paths, state: "running", launched: false };

  await writeJsonAtomic(paths.statusPath, {
    version: 1,
    state: "queued",
    jobPath,
    resultPath: paths.resultPath,
    updatedAt: new Date().toISOString(),
  });
  if (process.env.RESEARCH_REPORT_LOOP_HOOK_NO_SPAWN === "1") {
    return { ...paths, state: "queued", launched: false };
  }

  const scriptPath = fileURLToPath(import.meta.url);
  const child = spawn(
    process.execPath,
    [...process.execArgv, scriptPath, "run-loop-worker", jobPath, paths.statusPath, paths.resultPath, paths.lockPath],
    {
      cwd: path.dirname(jobPath),
      env: { ...process.env, RESEARCH_REPORT_LOOP_LAUNCHER: "workbuddy-host-hook" },
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    },
  );
  child.unref();
  return { ...paths, state: "queued", launched: true };
}

async function handleReportLoopJobWrite(toolName, toolInput, sessionId) {
  if (!WRITE_TOOL_PATTERN.test(toolName)) return undefined;
  const targetValue = toolInput?.file_path ?? toolInput?.filePath ?? toolInput?.path;
  if (typeof targetValue !== "string" || !targetValue.trim()) return undefined;
  const target = path.resolve(targetValue.trim());
  let stat;
  try {
    stat = await fs.stat(target);
  } catch {
    return undefined;
  }
  if (!stat.isFile() || stat.size > 1024 * 1024) return undefined;
  let payload;
  try {
    payload = JSON.parse(await fs.readFile(target, "utf8"));
  } catch {
    return undefined;
  }
  if (payload?.schemaVersion !== 2 || !payload?.v1ArtifactPath || !payload?.outputPath) return undefined;
  const sidecar = `${target}${REPORT_LOOP_SIDECAR_SUFFIX}`;
  await writeJsonAtomic(sidecar, { version: 1, sessionId });
  return launchReportLoopHostWorker(target, reportLoopArtifactPaths(target, payload));
}

async function runReportLoopWorker() {
  const [jobPath, statusPath, resultPath, lockPath] = process.argv.slice(3);
  if (![jobPath, statusPath, resultPath, lockPath].every(Boolean)) {
    throw new Error("missing report loop worker paths");
  }
  const launcher = new ReportLoopLauncher();
  try {
    await writeJsonAtomic(statusPath, {
      version: 1,
      state: "running",
      jobPath,
      resultPath,
      updatedAt: new Date().toISOString(),
    });
    const result = await launcher.run(jobPath);
    await writeJsonAtomic(resultPath, {
      ...result,
      jobPath,
      finishedAt: new Date().toISOString(),
    });
    await writeJsonAtomic(statusPath, {
      version: 1,
      state: result?.status === "error" ? "failed" : "completed",
      jobPath,
      resultPath,
      updatedAt: new Date().toISOString(),
    });
  } catch (error) {
    const failure = {
      status: "error",
      reason: "report_loop_host_worker_failed",
      detail: error?.message || String(error),
      jobPath,
      finishedAt: new Date().toISOString(),
    };
    await writeJsonAtomic(resultPath, failure);
    await writeJsonAtomic(statusPath, {
      version: 1,
      state: "failed",
      jobPath,
      resultPath,
      reason: failure.reason,
      updatedAt: new Date().toISOString(),
    });
  } finally {
    await launcher.destroy();
    await fs.unlink(lockPath).catch(() => {});
  }
}

async function waitForReportLoopResult() {
  const [resultPath, statusPath] = process.argv.slice(3);
  if (![resultPath, statusPath].every(Boolean)) {
    throw new Error("missing report loop wait paths");
  }
  const deadline = Date.now() + REPORT_LOOP_WAIT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    try {
      const raw = await fs.readFile(resultPath, "utf8");
      JSON.parse(raw);
      process.stdout.write(raw.endsWith("\n") ? raw : `${raw}\n`);
      return;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    const status = await readJson(statusPath);
    if (status?.state === "failed" || status?.state === "completed") {
      throw new Error(`report loop ${status.state} without result file`);
    }
    await new Promise((resolve) => setTimeout(resolve, REPORT_LOOP_WAIT_INTERVAL_MS));
  }
  throw new Error("report loop result wait timed out");
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
  const reportContext = state.active && REPORT_CONTEXT_PATTERN.test(prompt);
  const memoryManagement = MEMORY_MANAGEMENT_PATTERN.test(prompt) && !revisionFeedback;

  if (reportTask) state.active = true;
  if (!reportTask && !memoryManagement && (classified.relevant || reportContext)
    && (state.reportProduced || revisionFeedback || reportContext || (state.active && longTermPreference))) {
    state.active = true;
    state.capturePending = true;
    state.pendingFeedback = classified.relevant ? classified.writingText : prompt;
    state.pendingKind = classified.relevant ? "writing" : "context";
  }

  await saveState(state);
  const systemMessage = state.capturePending
    ? [
      state.pendingKind === "context"
        ? `检测到报告相关背景或实体纠正：${state.pendingFeedback}。`
        : `检测到用户对报告写法的反馈：${state.pendingFeedback}。`,
      state.pendingKind === "context"
        ? "无需为纯背景纠正改写报告；直接通过 Agent/Task 委派 research-report-memory-curator 执行 operation=capture。"
        : "先落实当前报告修改，再通过 Agent/Task 委派 research-report-memory-curator 执行 operation=capture。",
      "这是独立的 report-memory-v2（L0 Writing Episode / L1 Atom / L2B Rubrics），不是 WorkBuddy MEMORY.md 或项目日志。",
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

  const loopLaunch = successful
    ? await handleReportLoopJobWrite(actualToolName, toolInput, state.sessionId)
    : undefined;
  if (loopLaunch) {
    state.active = true;
    state.reportProduced = true;
    message = [
      loopLaunch.state === "completed"
        ? "Report Loop 已有可用结果。"
        : "Report Loop 已由插件 Hook 在宿主侧启动。",
      `结果文件：${loopLaunch.resultPath}。`,
      `后台等待命令：${reportLoopWaitCommand(loopLaunch.resultPath, loopLaunch.statusPath)}。`,
      "立即用 Bash 工具执行该命令并设置 run_in_background=true，保存返回的 task_id。",
      "收到 <task-notification> 后调用 TaskOutput(task_id) 读取完整结果；不要自行轮询文件。",
      "不要搜索或调用 report_loop_run，不要检查 MCP，也不要自行运行或测试 Runner。",
    ].join(" ");
  }

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
      state.pendingKind = "writing";
      message = "Report Memory Capture 已完成。";
    } else if (MEMORY_AGENT_FAILURE_MARKER.test(serialized) || !successful) {
      state.capturePending = false;
      state.pendingFeedback = "";
      state.pendingKind = "writing";
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
  if (mode === "run-loop-worker") return runReportLoopWorker();
  if (mode === "wait-loop-result") return waitForReportLoopResult();
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
