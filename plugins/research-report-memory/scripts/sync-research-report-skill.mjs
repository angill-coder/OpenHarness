#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = path.resolve(import.meta.dirname, "..");
const integrationBlock = `## Memory 调度\n\n执行本 Skill 时，必须同时读取并遵守 \`references/memory-orchestration.md\`。该文件是独立的 Memory 调度契约，不属于写作内容；不得将其中的流程、工具或规则写入报告正文。\n`;

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function readRequired(filePath, label) {
  if (!filePath) fail(`缺少 ${label} 路径。`);
  const absolute = path.resolve(filePath);
  if (!fs.existsSync(absolute)) fail(`${label} 不存在：${absolute}`);
  const content = fs.readFileSync(absolute, "utf8");
  if (!content.trim()) fail(`${label} 为空：${absolute}`);
  return content.endsWith("\n") ? content : `${content}\n`;
}

function composeSkill(upstreamSkill) {
  if (!/^---\n[\s\S]*?^name:\s*research-report\s*$[\s\S]*?^---$/mu.test(upstreamSkill)) {
    fail("SKILL.md frontmatter 必须声明 name: research-report。");
  }
  if (/references\/memory-orchestration\.md/u.test(upstreamSkill)) {
    fail("输入 SKILL.md 必须是纯写作上游版本，不能已经包含 Memory Overlay。");
  }
  const heading = upstreamSkill.match(/^# .+$/mu);
  if (!heading || heading.index === undefined) fail("SKILL.md 缺少一级标题。");
  const insertionPoint = heading.index + heading[0].length;
  return `${upstreamSkill.slice(0, insertionPoint)}\n\n${integrationBlock}\n${upstreamSkill.slice(insertionPoint).replace(/^\n+/, "")}`;
}

const [skillPath, instructionsPath] = process.argv.slice(2);
const upstreamSkill = readRequired(skillPath, "SKILL.md");
const upstreamInstructions = readRequired(instructionsPath, "instructions.md");
const finalSkill = composeSkill(upstreamSkill);

const upstreamDir = path.join(root, "upstream/research-report");
const finalDir = path.join(root, "skills/research-report");
fs.mkdirSync(upstreamDir, { recursive: true });
fs.mkdirSync(path.join(finalDir, "references"), { recursive: true });
fs.writeFileSync(path.join(upstreamDir, "SKILL.md"), upstreamSkill);
fs.writeFileSync(path.join(upstreamDir, "instructions.md"), upstreamInstructions);
fs.writeFileSync(path.join(finalDir, "SKILL.md"), finalSkill);
fs.writeFileSync(path.join(finalDir, "references/instructions.md"), upstreamInstructions);

process.stdout.write("research-report 写作 Skill 已更新；Memory Overlay 已自动合成。\n");
