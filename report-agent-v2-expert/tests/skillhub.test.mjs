import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const skillHub = path.resolve(root, "../report-agent-v2-skillhub");
const files = (dir, prefix = "") => fs.readdirSync(dir, {withFileTypes: true})
  .filter(e => ![".DS_Store", "__pycache__", "__MACOSX"].includes(e.name) && !e.name.startsWith("._"))
  .flatMap(e => e.isDirectory() ? files(path.join(dir, e.name), path.posix.join(prefix, e.name)) : [path.posix.join(prefix, e.name)]);
const read = (dir, relative) => fs.readFileSync(path.join(dir, relative), "utf8");
const manifest = (dir) => JSON.parse(read(dir, ".codebuddy-plugin/plugin.json"));

test("registry-only bootstrap preserves configs and handles first use, update and errors", () => {
  const python = [process.env.PYTHON, "python3", "python"].filter(Boolean).find(command =>
    spawnSync(command, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"], {timeout: 10000}).status === 0);
  assert.ok(python, "Tests require Python 3.9+");
  const result = spawnSync(python, ["-B", path.join(root, "tests/register_agents_test.py"), "-v"], {encoding: "utf8", timeout: 30000});
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("checked-in SkillHub package has one main Skill, valid links and no local install metadata", () => {
  assert.equal(read(skillHub, "SKILL.md"), read(root, "skills/research-report-agent-v2/SKILL.md"));
  assert.equal(manifest(skillHub).agents.length, 6);
  assert.deepEqual(manifest(skillHub).skills, ["./"]);
  assert.equal(files(skillHub).filter(p => path.basename(p) === "SKILL.md").length, 1);
  for (const filename of files(skillHub).filter(p => p.endsWith(".md"))) {
    for (const [, link] of read(skillHub, filename).matchAll(/\]\(([^)]+)\)/gu)) {
      if (/^(?:https?:|#|<)/u.test(link)) continue;
      assert.ok(fs.existsSync(path.resolve(skillHub, path.dirname(filename), link.split("#")[0])), `${filename}: ${link}`);
    }
  }
});

test("four packages share workflow content and release versions with only explicit packaging differences", () => {
  const plugin = path.resolve(root, "../report-agent-v2-plugin/plugins/report-agent-v2");
  const source = path.resolve(root, "../report-agent-v2-source");
  const workflowFiles = ["agents", "skills", "resources", "rubrics"].flatMap(group => files(path.join(root, group), group));
  const sourceFiles = workflowFiles.filter(name => !name.startsWith("resources/workbuddy/") && !name.endsWith("/first-use.md"));
  const skillHubPath = name => name.replace(/^skills\/research-report-agent-v2\//u, "");
  const skillHubText = (name, text) => name.endsWith(".md") ? text
    .replaceAll("../../../resources/", "../resources/")
    .replaceAll("../skills/research-report-agent-v2/references/", "../references/") : text;
  assert.deepEqual(files(source).sort(), [...sourceFiles, "README.md"].sort());
  assert.deepEqual(files(skillHub).sort(), [...workflowFiles.map(skillHubPath), "README.md", ".codebuddy-plugin/plugin.json"].sort());
  for (const name of workflowFiles) {
    assert.equal(read(plugin, name), read(root, name), `Plugin out of sync: ${name}`);
    assert.equal(read(skillHub, skillHubPath(name)), skillHubText(name, read(root, name)), `SkillHub out of sync: ${name}`);
  }
  for (const name of sourceFiles) {
    const expected = name === "skills/research-report-agent-v2/SKILL.md"
      ? read(root, name).replace(/## 首次启用\n[\s\S]*?(?=## 执行步骤)/u, "") : read(root, name);
    assert.equal(read(source, name), expected, `Source out of sync: ${name}`);
  }
  const canonical = manifest(root);
  assert.equal(canonical.version, JSON.parse(read(root, "package.json")).version);
  for (const dir of [plugin, skillHub]) {
    const actual = manifest(dir);
    for (const key of ["name", "version", "agents"]) assert.deepEqual(actual[key], canonical[key], `${dir}: ${key}`);
  }
});
