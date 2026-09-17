/**
 * 读取写作记忆的开关状态。
 *
 * 记忆本体由 `report-memory-agent` 维护在用户主目录下可见的
 * `ReportAgentMemory/`（纯 Markdown）。这里只做**只读**的开关探测，供 Hook
 * 判断是否需要提醒 Capture——不写入、不初始化、不猜测其他状态。
 *
 * 目录缺失或 `MEMORY.md` 读不到时按"启用"处理：记忆尚未初始化是正常的首次
 * 使用状态，Hook 照常提醒，真正的初始化由记忆管理员完成。
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const MEMORY_DIR_NAME = "ReportAgentMemory";
export const MEMORY_FILE_NAME = "MEMORY.md";

/** 记忆根目录：环境变量 > 主目录下 ReportAgentMemory/。 */
export function resolveMemoryRoot(): string {
  const configured = process.env.REPORT_AGENT_MEMORY_DIR?.trim();
  if (configured) {
    return configured.replace(/^~(?=$|\/)/u, os.homedir());
  }
  return path.join(os.homedir(), MEMORY_DIR_NAME);
}

/** 读取 `MEMORY.md` 顶部的 `enabled:` 开关。 */
export function isMemoryEnabled(memoryRoot: string = resolveMemoryRoot()): boolean {
  const memoryFile = path.join(memoryRoot, MEMORY_FILE_NAME);
  let raw: string;
  try {
    raw = fs.readFileSync(memoryFile, "utf8");
  } catch {
    // 尚未初始化：按默认启用处理
    return true;
  }
  const match = raw.match(/^enabled:\s*(true|false)\s*$/mu);
  return match ? match[1].toLowerCase() === "true" : true;
}
