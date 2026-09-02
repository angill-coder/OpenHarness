import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const releaseRoot = path.join(root, "release");
const target = path.join(releaseRoot, "report-loop-v2-expert");
const included = [
  ".codebuddy-plugin",
  "agents",
  "skills",
  "rubrics",
  "README.md",
];

fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(target, { recursive: true });
for (const entry of included) {
  fs.cpSync(path.join(root, entry), path.join(target, entry), { recursive: true });
}

process.stdout.write(`WorkBuddy Native V2 Expert built: ${target}\n`);
