import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function resolveBaseRubricPath(moduleDir: string): string {
  const configured = process.env.RESEARCH_REPORT_BASE_RUBRIC_PATH?.trim();
  if (configured) return path.resolve(configured.replace(/^~(?=$|\/)/u, os.homedir()));
  const candidates = [
    path.resolve(moduleDir, "../rubrics/v2_rubric_research.json"),
    path.resolve(moduleDir, "../../rubrics/v2_rubric_research.json"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? candidates[0];
}
