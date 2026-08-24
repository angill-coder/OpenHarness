import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { resolveBaseRubricPath } from "../src/asset-paths.ts";

test("base rubric resolver works from bundled dist and source module layouts", (t) => {
  const previous = process.env.RESEARCH_REPORT_BASE_RUBRIC_PATH;
  delete process.env.RESEARCH_REPORT_BASE_RUBRIC_PATH;
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "report-memory-assets-"));
  t.after(() => {
    fs.rmSync(root, { recursive: true, force: true });
    if (previous === undefined) delete process.env.RESEARCH_REPORT_BASE_RUBRIC_PATH;
    else process.env.RESEARCH_REPORT_BASE_RUBRIC_PATH = previous;
  });
  const rubric = path.join(root, "rubrics/v2_rubric_research.json");
  fs.mkdirSync(path.dirname(rubric), { recursive: true });
  fs.writeFileSync(rubric, "{}\n");
  fs.mkdirSync(path.join(root, "dist"), { recursive: true });
  fs.mkdirSync(path.join(root, "mcp/src"), { recursive: true });

  assert.equal(resolveBaseRubricPath(path.join(root, "dist")), rubric);
  assert.equal(resolveBaseRubricPath(path.join(root, "mcp/src")), rubric);
});
