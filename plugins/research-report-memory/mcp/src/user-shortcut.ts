import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

export const USER_SHORTCUT_NAME = "Research Report Memory";

function usesDefaultDataDir(dataDir: string): boolean {
  return path.resolve(dataDir) === path.resolve(path.join(os.homedir(), ".research-report-memory-v2-mvp"));
}

export function resolveUserShortcut(dataDir: string): string | undefined {
  const configured = process.env.RESEARCH_REPORT_MEMORY_SHORTCUT?.trim();
  if (configured === "0" || configured === "false") return undefined;
  if (configured) return configured.replace(/^~(?=$|\/)/u, os.homedir());
  if (!usesDefaultDataDir(dataDir)) return undefined;
  return path.join(os.homedir(), USER_SHORTCUT_NAME);
}

export function resolveLegacyDocumentsShortcut(dataDir: string): string | undefined {
  return usesDefaultDataDir(dataDir)
    ? path.join(os.homedir(), "Documents", USER_SHORTCUT_NAME)
    : undefined;
}

export async function ensureUserShortcut(target: string, shortcut: string): Promise<"created" | "unchanged" | "skipped"> {
  await fs.mkdir(path.dirname(shortcut), { recursive: true });
  try {
    const status = await fs.lstat(shortcut);
    if (!status.isSymbolicLink()) return "skipped";
    const currentTarget = await fs.readlink(shortcut);
    const resolvedTarget = path.resolve(path.dirname(shortcut), currentTarget);
    return resolvedTarget === path.resolve(target) ? "unchanged" : "skipped";
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  await fs.symlink(target, shortcut, process.platform === "win32" ? "junction" : "dir");
  return "created";
}

export async function removeManagedShortcut(target: string, shortcut: string): Promise<"removed" | "missing" | "skipped"> {
  try {
    const status = await fs.lstat(shortcut);
    if (!status.isSymbolicLink()) return "skipped";
    const currentTarget = await fs.readlink(shortcut);
    const resolvedTarget = path.resolve(path.dirname(shortcut), currentTarget);
    if (resolvedTarget !== path.resolve(target)) return "skipped";
    await fs.unlink(shortcut);
    return "removed";
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return "missing";
    throw error;
  }
}
