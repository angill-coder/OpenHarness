import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runner = path.join(root, "scripts", process.platform === "win32" ? "run-python.cmd" : "run-python.sh");
const command = process.platform === "win32" ? "cmd.exe" : "sh";
const args = process.platform === "win32" ? ["/d", "/c", runner, ...process.argv.slice(2)] : [runner, ...process.argv.slice(2)];
const result = spawnSync(command, args, { cwd: root, stdio: "inherit" });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
