import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  ensureUserShortcut,
  removeManagedShortcut,
  resolveLegacyDocumentsShortcut,
  resolveUserShortcut,
} from "../src/user-shortcut.ts";

test("uses the cross-platform home directory rather than Documents or WorkBuddy", () => {
  const dataDir = path.join(os.homedir(), ".research-report-memory-v2-0821");
  assert.equal(resolveUserShortcut(dataDir), path.join(os.homedir(), "Research Report Memory V2-0821"));
  assert.equal(resolveLegacyDocumentsShortcut(dataDir), path.join(os.homedir(), "Documents", "Research Report Memory V2-0821"));
  assert.equal(resolveUserShortcut(path.join(os.tmpdir(), "custom-memory")), undefined);
});

test("creates an idempotent user shortcut to the L2B repository", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-shortcut-"));
  const target = path.join(root, "memory", "personal", "default");
  const shortcut = path.join(root, "Documents", "Research Report Memory");
  fs.mkdirSync(target, { recursive: true });
  try {
    assert.equal(await ensureUserShortcut(target, shortcut), "created");
    assert.equal(fs.realpathSync(shortcut), fs.realpathSync(target));
    assert.equal(await ensureUserShortcut(target, shortcut), "unchanged");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("never overwrites an existing user file or directory", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-shortcut-conflict-"));
  const target = path.join(root, "memory", "personal", "default");
  const shortcut = path.join(root, "Documents", "Research Report Memory");
  fs.mkdirSync(target, { recursive: true });
  fs.mkdirSync(shortcut, { recursive: true });
  try {
    assert.equal(await ensureUserShortcut(target, shortcut), "skipped");
    assert.equal(fs.lstatSync(shortcut).isSymbolicLink(), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("removes only the legacy shortcut managed by this plugin", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "research-report-memory-shortcut-legacy-"));
  const target = path.join(root, "memory", "personal", "default");
  const otherTarget = path.join(root, "other");
  const managed = path.join(root, "Documents", "Research Report Memory");
  const unrelated = path.join(root, "Documents", "Unrelated");
  fs.mkdirSync(target, { recursive: true });
  fs.mkdirSync(otherTarget, { recursive: true });
  fs.mkdirSync(path.dirname(managed), { recursive: true });
  fs.symlinkSync(target, managed, "dir");
  fs.symlinkSync(otherTarget, unrelated, "dir");
  try {
    assert.equal(await removeManagedShortcut(target, managed), "removed");
    assert.equal(fs.existsSync(managed), false);
    assert.equal(await removeManagedShortcut(target, unrelated), "skipped");
    assert.equal(fs.existsSync(unrelated), true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
