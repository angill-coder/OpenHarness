import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { buildSkillHub } from "../scripts/build-skillhub.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("registry-only bootstrap preserves configs and handles first use, update and errors", () => {
  const python = [process.env.PYTHON, "python3", "python"].filter(Boolean).find(command =>
    spawnSync(command, ["-c", "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"], {timeout: 10000}).status === 0);
  assert.ok(python, "Tests require Python 3.9+");
  const result = spawnSync(python, ["-B", path.join(root, "tests/register_agents_test.py"), "-v"], {encoding: "utf8", timeout: 30000});
  assert.equal(result.status, 0, result.stdout + result.stderr);
});

test("SkillHub flattens one unchanged main Skill with valid links and the same agents/resources", () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "report-skillhub-test-"));
  try {
    const output = buildSkillHub(path.join(temp, "package"));
    assert.deepEqual(fs.readFileSync(path.join(output, "SKILL.md")), fs.readFileSync(path.join(root, "skills/research-report-agent-v2/SKILL.md")));
    const manifest = JSON.parse(fs.readFileSync(path.join(output, ".codebuddy-plugin/plugin.json"), "utf8"));
    assert.equal(manifest.agents.length, 6);
    assert.deepEqual(manifest.skills, ["./"]);
    assert.equal(fs.existsSync(path.join(output, ".codebuddy-plugin/marketplace.json")), false);
    assert.equal(fs.existsSync(path.join(output, "_skillhub_meta.json")), false);
    assert.equal(fs.existsSync(path.join(output, "skills")), false);
    const files = (dir) => fs.readdirSync(dir, {withFileTypes: true}).flatMap(e => e.isDirectory() ? files(path.join(dir, e.name)) : [path.join(dir, e.name)]);
    assert.equal(files(output).filter(p => path.basename(p) === "SKILL.md").length, 1);
    for (const filename of files(output).filter(p => p.endsWith(".md"))) {
      for (const [, link] of fs.readFileSync(filename, "utf8").matchAll(/\]\(([^)]+)\)/gu)) {
        if (/^(?:https?:|#|<)/u.test(link)) continue;
        assert.ok(fs.existsSync(path.resolve(path.dirname(filename), link.split("#")[0])), `${filename}: ${link}`);
      }
    }
    for (const filename of files(path.join(root, "resources"))) {
      const relative = path.relative(root, filename);
      assert.deepEqual(fs.readFileSync(path.join(output, relative)), fs.readFileSync(filename));
    }
    for (const agent of manifest.agents) {
      assert.equal(fs.readFileSync(path.join(output, agent), "utf8"), fs.readFileSync(path.join(root, agent), "utf8").replaceAll("../skills/research-report-agent-v2/references/", "../references/"));
    }
  } finally { fs.rmSync(temp, {recursive: true, force: true}); }
});
