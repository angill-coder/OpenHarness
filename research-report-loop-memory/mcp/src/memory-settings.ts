import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export interface MemorySettings {
  schemaVersion: 1;
  memoryEnabled: boolean;
  updatedAt?: string;
}

export function resolveMemoryDataDir(environment: NodeJS.ProcessEnv = process.env): string {
  const configured = environment.RESEARCH_REPORT_MEMORY_V2_0821_DIR?.trim();
  if (configured) return configured.replace(/^~(?=$|[\\/])/u, os.homedir());
  return path.join(os.homedir(), ".research-report-memory-v2-0821");
}

export function memorySettingsPath(dataDir = resolveMemoryDataDir()): string {
  return path.join(dataDir, "settings.json");
}

export function readMemorySettings(dataDir = resolveMemoryDataDir()): MemorySettings {
  try {
    const parsed = JSON.parse(fs.readFileSync(memorySettingsPath(dataDir), "utf8").replace(/^\uFEFF/u, ""));
    if (parsed?.schemaVersion === 1 && typeof parsed.memoryEnabled === "boolean") {
      return {
        schemaVersion: 1,
        memoryEnabled: parsed.memoryEnabled,
        ...(typeof parsed.updatedAt === "string" ? { updatedAt: parsed.updatedAt } : {}),
      };
    }
  } catch {
    // Missing or invalid settings fail closed: Memory is opt-in.
  }
  return { schemaVersion: 1, memoryEnabled: false };
}

export function isMemoryEnabled(dataDir = resolveMemoryDataDir()): boolean {
  return readMemorySettings(dataDir).memoryEnabled;
}

export function writeMemorySettings(memoryEnabled: boolean, dataDir = resolveMemoryDataDir()): MemorySettings {
  const settings: MemorySettings = {
    schemaVersion: 1,
    memoryEnabled,
    updatedAt: new Date().toISOString(),
  };
  const target = memorySettingsPath(dataDir);
  fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 });
  const temporary = `${target}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(settings, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, target);
  return settings;
}
