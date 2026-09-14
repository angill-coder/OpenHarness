import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const releaseRoot = path.join(root, "release");
const version = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")).version;
const target = path.join(releaseRoot, `report-agent-v2-expert-${version}`);
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
  fs.cpSync(path.join(root, entry), path.join(target, entry), {
    recursive: true,
    filter: (filename) => ![".DS_Store", "__MACOSX"].includes(path.basename(filename)) && !path.basename(filename).startsWith("._"),
  });
}

process.stdout.write(`WorkBuddy Native V2 Expert built: ${target}\n`);
