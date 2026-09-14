import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Only the installation wrapper changes. All workflow assets stay byte-identical.
export function buildPlugin(outputDirectory) {
  const source = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
  const target = outputDirectory ?? path.join(root, "release", `report-agent-v2-plugin-${source.version}`);
  const pluginRoot = path.join(target, "plugins", source.name);
  const manifest = Object.fromEntries(
    ["name", "version", "author", "license", "keywords", "agents", "skills"].map((key) => [key, source[key]]),
  );
  manifest.description = "原生研究报告 V2：子代理先整理结构化论据，主 Agent 调度 Writer 持续写作，再完成记忆与评测。使用 research-report-agent-v2 Skill 启动。";
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(path.join(pluginRoot, ".codebuddy-plugin"), { recursive: true });
  for (const entry of ["agents", "skills", "rubrics"]) {
    fs.cpSync(path.join(root, entry), path.join(pluginRoot, entry), {
      recursive: true,
      filter: (filename) => ![".DS_Store", "__MACOSX"].includes(path.basename(filename)) && !path.basename(filename).startsWith("._"),
    });
  }
  fs.writeFileSync(path.join(pluginRoot, ".codebuddy-plugin/plugin.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  fs.mkdirSync(path.join(target, ".codebuddy-plugin"), { recursive: true });
  fs.writeFileSync(path.join(target, ".codebuddy-plugin/marketplace.json"), `${JSON.stringify({
    name: "report-agent-v2-local",
    owner: source.author,
    plugins: [{ name: source.name, source: `./plugins/${source.name}`, description: manifest.description }],
  }, null, 2)}\n`);
  for (const directory of [target, pluginRoot]) {
    fs.copyFileSync(path.join(root, "packaging/plugin-README.md"), path.join(directory, "README.md"));
  }
  return target;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(`Repository plugin synchronized: ${buildPlugin(path.resolve(root, "../report-agent-v2-plugin"))}\n`);
  process.stdout.write(`WorkBuddy Native V2 plugin built: ${buildPlugin()}\n`);
}
