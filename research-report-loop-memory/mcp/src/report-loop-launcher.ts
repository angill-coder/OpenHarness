import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUTPUT_LIMIT = 1_000_000;
const DEFAULT_TIMEOUT_MS = 3_700_000;

export type RunnerPaths = {
  pluginRoot: string;
  launcher: string;
  runner: string;
};

function existingRoot(candidate: string): RunnerPaths | undefined {
  const pluginRoot = path.resolve(candidate);
  const launcher = path.join(
    pluginRoot,
    "scripts",
    process.platform === "win32" ? "run-python.cmd" : "run-python.sh",
  );
  const runner = path.join(pluginRoot, "mcp", "report_loop", "runner.py");
  if (fs.statSync(launcher, { throwIfNoEntry: false })?.isFile()
      && fs.statSync(runner, { throwIfNoEntry: false })?.isFile()) {
    return { pluginRoot, launcher, runner };
  }
  return undefined;
}

export function resolveRunnerPaths(moduleUrl = import.meta.url): RunnerPaths {
  const moduleDir = path.dirname(fileURLToPath(moduleUrl));
  const candidates = [
    process.env.CODEBUDDY_PLUGIN_ROOT,
    path.resolve(moduleDir, ".."),
    path.resolve(moduleDir, "..", ".."),
  ].filter((value): value is string => Boolean(value?.trim()));
  for (const candidate of candidates) {
    const resolved = existingRoot(candidate);
    if (resolved) return resolved;
  }
  throw new Error("report_loop_runner_not_found");
}

export function runnerCommand(paths: RunnerPaths, platform = process.platform) {
  if (platform === "win32") {
    return {
      command: "cmd.exe",
      args: ["/d", "/s", "/c", `""${paths.launcher}" "${paths.runner}" --job "%REPORT_LOOP_JOB_PATH%""`],
      usesJobEnvironment: true,
    };
  }
  return {
    command: "sh",
    args: [paths.launcher, paths.runner, "--job"],
    usesJobEnvironment: false,
  };
}

function appendTail(current: string, chunk: Buffer | string) {
  const combined = current + chunk.toString();
  return combined.length <= OUTPUT_LIMIT ? combined : combined.slice(-OUTPUT_LIMIT);
}

function parseRunnerPayload(stdout: string, stderr: string, exitCode: number | null) {
  for (const line of stdout.trim().split(/\r?\n/u).reverse()) {
    try {
      const parsed = JSON.parse(line);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Runner may emit non-JSON diagnostics before its final JSON line.
    }
  }
  return {
    status: "error",
    reason: "report_loop_runner_invalid_output",
    exitCode,
    detail: (stderr || stdout).trim().slice(-4000),
  };
}

export class ReportLoopLauncher {
  private readonly children = new Set<ReturnType<typeof spawn>>();

  async run(jobPathValue: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Record<string, unknown>> {
    const jobPath = path.resolve(jobPathValue);
    if (!path.isAbsolute(jobPathValue) || !fs.statSync(jobPath, { throwIfNoEntry: false })?.isFile()) {
      return { status: "error", reason: "report_loop_job_not_found", jobPath };
    }
    const paths = resolveRunnerPaths();
    const invocation = runnerCommand(paths);
    const env = {
      ...process.env,
      REPORT_LOOP_JOB_PATH: jobPath,
      RESEARCH_REPORT_LOOP_LAUNCHER: "mcp-host",
    };
    const args = invocation.usesJobEnvironment
      ? invocation.args
      : [...invocation.args, jobPath];

    return await new Promise((resolve) => {
      let timer: NodeJS.Timeout | undefined;
      const child = spawn(invocation.command, args, {
        cwd: path.dirname(jobPath),
        env,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
      this.children.add(child);
      let stdout = "";
      let stderr = "";
      let settled = false;
      const finish = (payload: Record<string, unknown>) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        this.children.delete(child);
        resolve(payload);
      };
      child.stdout?.on("data", (chunk) => { stdout = appendTail(stdout, chunk); });
      child.stderr?.on("data", (chunk) => { stderr = appendTail(stderr, chunk); });
      child.once("error", (error) => finish({
        status: "error",
        reason: "report_loop_runner_launch_failed",
        detail: error.message,
      }));
      child.once("close", (code) => finish(parseRunnerPayload(stdout, stderr, code)));
      timer = setTimeout(() => {
        child.kill("SIGTERM");
        finish({ status: "error", reason: "report_loop_runner_timeout" });
      }, timeoutMs);
      timer.unref();
    });
  }

  async destroy() {
    for (const child of this.children) child.kill("SIGTERM");
    this.children.clear();
  }
}
