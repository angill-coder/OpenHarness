import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

type ReflectionPreference = { enabled?: boolean };

function expandHome(value: string): string {
  if (value === "~") return os.homedir();
  if (value.startsWith(`~${path.sep}`) || value.startsWith("~/") || value.startsWith("~\\")) {
    return path.join(os.homedir(), value.slice(2));
  }
  return value;
}

function packagedPluginRoot(moduleUrl: string, environment: NodeJS.ProcessEnv): string | undefined {
  const injected = environment.CODEBUDDY_PLUGIN_ROOT?.trim();
  if (injected) return expandHome(injected);

  const moduleDirectory = path.dirname(fileURLToPath(moduleUrl));
  if (path.basename(moduleDirectory) !== "dist") return undefined;
  return path.dirname(moduleDirectory);
}

function readPreference(settingsPath: string): ReflectionPreference | undefined {
  try {
    return JSON.parse(fs.readFileSync(settingsPath, "utf8").replace(/^\uFEFF/u, "")) as ReflectionPreference;
  } catch {
    return undefined;
  }
}

export function reflectionAutoEnablePlan(
  moduleUrl: string,
  environment: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
) {
  if (environment.RESEARCH_REPORT_REFLECTION_AUTO_ENABLE === "0") {
    return { action: "skip" as const, reason: "disabled_by_environment" };
  }

  const pluginRoot = packagedPluginRoot(moduleUrl, environment);
  if (!pluginRoot) return { action: "skip" as const, reason: "not_packaged_plugin" };

  const dataDirectory = expandHome(
    environment.RESEARCH_REPORT_MEMORY_V2_0821_DIR?.trim()
      || path.join(os.homedir(), ".research-report-memory-v2-0821"),
  );
  const settingsPath = path.join(dataDirectory, "reflection", "schedule-settings.json");
  const preference = readPreference(settingsPath);
  if (preference?.enabled === false) return { action: "skip" as const, reason: "disabled_by_user" };
  if (preference?.enabled === true) return { action: "skip" as const, reason: "already_enabled" };

  if (platform === "darwin") {
    const installer = path.join(pluginRoot, "scripts", "install-reflection-macos.sh");
    if (!fs.existsSync(installer)) return { action: "skip" as const, reason: "installer_missing" };
    return { action: "install" as const, command: "/bin/sh", args: [installer], pluginRoot, dataDirectory };
  }
  if (platform === "win32") {
    const installer = path.join(pluginRoot, "scripts", "install-reflection-windows.ps1");
    if (!fs.existsSync(installer)) return { action: "skip" as const, reason: "installer_missing" };
    return {
      action: "install" as const,
      command: "powershell.exe",
      args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", installer],
      pluginRoot,
      dataDirectory,
    };
  }
  return { action: "skip" as const, reason: "unsupported_platform" };
}

export function autoEnableReflection(moduleUrl: string): void {
  const plan = reflectionAutoEnablePlan(moduleUrl);
  if (plan.action !== "install") return;

  try {
    const child = spawn(plan.command, plan.args, {
      cwd: plan.pluginRoot,
      detached: true,
      stdio: "ignore",
      windowsHide: true,
      env: {
        ...process.env,
        RESEARCH_REPORT_MEMORY_V2_0821_DIR: plan.dataDirectory,
      },
    });
    child.on("error", (error) => {
      console.error(`[report-memory-v2] Reflection auto-enable failed: ${error.message}`);
    });
    child.unref();
  } catch (error) {
    console.error(`[report-memory-v2] Reflection auto-enable failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}
