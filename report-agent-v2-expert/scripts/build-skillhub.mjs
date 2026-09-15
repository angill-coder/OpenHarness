import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { buildPlugin } from "./build-plugin.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Flatten the same main Skill; no extra wrapper Skill or marketplace registration.
export function buildSkillHub(outputDirectory) {
  const source = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
  const target = outputDirectory ?? path.join(root, "release", `report-agent-v2-skillhub-${source.version}`);
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "report-skillhub-build-"));
  try {
    const marketplace = buildPlugin(path.join(temp, "marketplace"));
    fs.rmSync(target, { recursive: true, force: true });
    fs.cpSync(path.join(marketplace, "plugins", source.name), target, { recursive: true });
    const skill = path.join(target, "skills/research-report-agent-v2");
    fs.copyFileSync(path.join(skill, "SKILL.md"), path.join(target, "SKILL.md"));
    fs.cpSync(path.join(skill, "references"), path.join(target, "references"), { recursive: true });
    fs.rmSync(path.join(target, "skills"), { recursive: true });
    for (const dir of ["references", "agents"]) {
      for (const entry of fs.readdirSync(path.join(target, dir))) {
        if (!entry.endsWith(".md")) continue;
        const filename = path.join(target, dir, entry);
        const content = fs.readFileSync(filename, "utf8")
          .replaceAll("../../../resources/", "../resources/")
          .replaceAll("../skills/research-report-agent-v2/references/", "../references/");
        fs.writeFileSync(filename, content);
      }
    }
    const manifestPath = path.join(target, ".codebuddy-plugin/plugin.json");
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    manifest.skills = ["./"];
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
    fs.copyFileSync(path.join(root, "packaging/skillhub-README.md"), path.join(target, "README.md"));
    return target;
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(`SkillHub package built: ${buildSkillHub()}\n`);
}
