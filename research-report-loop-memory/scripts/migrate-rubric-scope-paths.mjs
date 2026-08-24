#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

function canonicalScopeValue(value) {
  return String(value ?? "").normalize("NFKC").trim();
}

function readableSlug(value) {
  const normalized = canonicalScopeValue(value)
    .toLocaleLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, "-")
    .replace(/^-+|-+$/gu, "");
  return normalized || crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 12);
}

function scopeStorageKey(value) {
  const canonical = canonicalScopeValue(value);
  const digest = crypto.createHash("sha256").update(canonical).digest("hex").slice(0, 12);
  return `${readableSlug(canonical)}--${digest}`;
}

function argumentValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function git(repository, args) {
  return execFileSync("git", args, { cwd: repository, encoding: "utf8" }).trim();
}

function readAt(repository, revision, relativePath) {
  return JSON.parse(git(repository, ["show", `${revision}:${relativePath}`]));
}

function main() {
  const dataDir = path.resolve(
    argumentValue("--data-dir")
      ?? process.env.RESEARCH_REPORT_MEMORY_V2_0821_DIR?.replace(/^~(?=\/|$)/u, os.homedir())
      ?? path.join(os.homedir(), ".research-report-memory-v2-0821"),
  );
  const repository = path.join(dataDir, "l2b-rubrics", "personal", "default");
  const apply = process.argv.includes("--apply");
  if (!fs.existsSync(path.join(repository, ".git"))) {
    process.stdout.write(`${JSON.stringify({ status: "empty", repository, moves: [] }, null, 2)}\n`);
    return;
  }
  const files = git(repository, ["ls-tree", "-r", "--name-only", "HEAD"])
    .split("\n")
    .filter((value) => /^(audiences|projects)\/.+\/rubrics\.json$/u.test(value));
  const moves = [];
  const conflicts = [];
  const targets = new Set();
  for (const source of files) {
    const document = readAt(repository, "HEAD", source);
    const root = document.scope === "audience" ? "audiences" : document.scope === "project" ? "projects" : undefined;
    const scopeValue = canonicalScopeValue(document.scopeValue);
    if (!root || !scopeValue) {
      conflicts.push({ source, reason: "invalid_scope_document" });
      continue;
    }
    const target = `${root}/${scopeStorageKey(scopeValue)}/rubrics.json`;
    if (source === target) continue;
    const historicalValues = new Set(
      git(repository, ["log", "--format=%H", "--", source])
        .split("\n")
        .filter(Boolean)
        .flatMap((revision) => {
          try { return [canonicalScopeValue(readAt(repository, revision, source).scopeValue)]; }
          catch { return []; }
        })
        .filter(Boolean),
    );
    if (historicalValues.size > 1) {
      conflicts.push({ source, reason: "historical_scope_collision", scopeValues: [...historicalValues] });
      continue;
    }
    if (targets.has(target) || fs.existsSync(path.join(repository, target))) {
      conflicts.push({ source, target, reason: "target_already_exists" });
      continue;
    }
    targets.add(target);
    moves.push({ source, target, scope: document.scope, scopeValue });
  }
  const result = { status: conflicts.length ? "blocked" : apply ? "migrated" : "dry_run", repository, moves, conflicts };
  if (conflicts.length || !apply || moves.length === 0) {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    if (conflicts.length) process.exitCode = 2;
    return;
  }
  if (git(repository, ["status", "--porcelain"])) {
    throw new Error("Rubric repository has uncommitted changes; migration aborted");
  }
  for (const move of moves) {
    fs.mkdirSync(path.dirname(path.join(repository, move.target)), { recursive: true, mode: 0o700 });
    execFileSync("git", ["mv", move.source, move.target], { cwd: repository });
  }
  execFileSync("git", ["commit", "-m", "memory: migrate collision-safe scope paths"], { cwd: repository });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

main();
