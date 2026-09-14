import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const target = path.resolve(root, '../report-agent-v2-source');
const files = directory => fs.readdirSync(directory, { recursive: true }).filter(p => fs.statSync(path.join(directory, p)).isFile()).sort();
test('raw source preserves workflow assets without installation wrappers', () => {
  for (const group of ['agents', 'skills', 'resources', 'rubrics', 'evals']) {
    const source = path.join(root, group), exported = path.join(target, group);
    assert.deepEqual(files(exported), files(source));
    for (const name of files(source)) assert.deepEqual(fs.readFileSync(path.join(exported, name)), fs.readFileSync(path.join(source, name)), `${group}/${name}`);
  }
  for (const name of files(target)) assert.doesNotMatch(name, /(^|\/)(\.codebuddy-plugin|\.claude-plugin|\.codex-plugin|release|node_modules|__pycache__|avatars|hooks)(\/|$)/);
});
