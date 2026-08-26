import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("WorkBuddy Expert remains a thin wrapper around the existing plugin", () => {
  const agent = fs.readFileSync(path.join(root, "expert/report-expert-v1.md"), "utf8");
  const frontmatter = agent.match(/^---\n([\s\S]*?)\n---/u)?.[1] ?? "";
  const builder = fs.readFileSync(path.join(root, "scripts/build-expert.mjs"), "utf8");

  assert.match(frontmatter, /^name: report-expert-v1$/mu);
  assert.match(frontmatter, /^skills: \[research-report-loop\]$/mu);
  assert.doesNotMatch(frontmatter, /^tools:/mu);
  assert.match(builder, /scripts\/build-release\.mjs/u);
  assert.match(builder, /report-expert-v2/u);
  assert.match(builder, /expertPluginName = "report-loop"/u);
  assert.match(builder, /mcp__report-expert-v2__/u);
  assert.match(builder, /bin\/run-node/u);
  assert.match(builder, /bin\/run-node\.cmd/u);
  assert.match(builder, /hooks\.json/u);
  assert.doesNotMatch(builder, /expertType:\s*"team"/u);
});
