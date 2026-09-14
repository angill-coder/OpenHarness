import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { buildPlugin } from "../scripts/build-plugin.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const readJson = (filename) => JSON.parse(fs.readFileSync(filename, "utf8"));

function files(directory, prefix = "") {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const relative = path.join(prefix, entry.name);
    return entry.isDirectory() ? files(path.join(directory, entry.name), relative) : [relative];
  });
}

test("checked-in plugin stays identical to a fresh build from Expert source", () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "report-agent-v2-parity-"));
  try {
    const expected = buildPlugin(path.join(temp, "package"));
    const actual = path.resolve(root, "../report-agent-v2-plugin");
    assert.ok(fs.existsSync(actual), "Run npm run build:plugin before committing");
    const expectedFiles = files(expected).sort();
    assert.deepEqual(files(actual).filter((name) => path.basename(name) !== ".DS_Store").sort(), expectedFiles);
    for (const relative of expectedFiles) {
      assert.deepEqual(fs.readFileSync(path.join(actual, relative)), fs.readFileSync(path.join(expected, relative)), `${relative}: run npm run build:plugin`);
    }
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("ordinary plugin is installable as a local marketplace with unchanged workflow assets", () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "report-agent-v2-plugin-"));
  const expertManifestPath = path.join(root, ".codebuddy-plugin/plugin.json");
  const before = fs.readFileSync(expertManifestPath);
  try {
    const target = buildPlugin(path.join(temp, "package"));
    const marketplace = readJson(path.join(target, ".codebuddy-plugin/marketplace.json"));
    assert.equal(marketplace.name, "report-agent-v2-local");
    assert.equal(marketplace.plugins.length, 1);
    const plugin = path.resolve(target, marketplace.plugins[0].source);
    const manifest = readJson(path.join(plugin, ".codebuddy-plugin/plugin.json"));
    const source = readJson(expertManifestPath);
    assert.equal(manifest.name, source.name);
    assert.equal(manifest.version, source.version);
    assert.deepEqual(manifest.agents, source.agents);
    assert.deepEqual(manifest.skills, source.skills);
    for (const key of ["expertType", "agentName", "plugin", "settings", "hooks", "mcpServers"]) {
      assert.equal(Object.hasOwn(manifest, key), false, key);
    }
    for (const entry of [...manifest.agents, ...manifest.skills]) {
      assert.ok(fs.existsSync(path.resolve(plugin, entry)), entry);
    }
    for (const group of ["agents", "skills", "resources", "rubrics"]) {
      const expected = files(path.join(root, group)).filter((name) => !name.split(path.sep).some((part) => part === ".DS_Store" || part === "__MACOSX" || part.startsWith("._")));
      assert.deepEqual(files(path.join(plugin, group)).sort(), expected.sort());
      for (const relative of expected) {
        assert.deepEqual(fs.readFileSync(path.join(plugin, group, relative)), fs.readFileSync(path.join(root, group, relative)), relative);
      }
    }
    assert.doesNotMatch(files(target).join("\n"), /node_modules|\.DS_Store|\.cmd$|\.ps1$|\.sh$/mu);
    assert.deepEqual(fs.readFileSync(expertManifestPath), before);
    // Rebuild removes stale generated files, without touching the Expert output.
    fs.writeFileSync(path.join(target, "stale.txt"), "old");
    buildPlugin(target);
    assert.equal(fs.existsSync(path.join(target, "stale.txt")), false);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
