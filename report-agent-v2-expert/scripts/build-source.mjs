import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const target = path.resolve(root, '../report-agent-v2-source');
// Export workflow source only. Never include installation metadata or user data.
fs.mkdirSync(target, { recursive: true });
for (const entry of ['agents', 'skills', 'resources', 'rubrics', 'evals']) {
  fs.cpSync(path.join(root, entry), path.join(target, entry), {
    recursive: true,
    filter: filename => !['.DS_Store', '__pycache__'].includes(path.basename(filename)) && !path.basename(filename).endsWith('-workspace'),
  });
}
fs.mkdirSync(path.join(target, 'tests'), { recursive: true });
for (const entry of ['source-inventory.test.mjs', 'source_inventory_test.py', 'workspace-contract.test.mjs']) {
  fs.copyFileSync(path.join(root, 'tests', entry), path.join(target, 'tests', entry));
}
fs.copyFileSync(path.join(root, 'packaging/source-README.md'), path.join(target, 'README.md'));
fs.writeFileSync(path.join(target, 'package.json'), JSON.stringify({ name: 'report-agent-v2-source', version: '0.3.0', private: true, type: 'module', scripts: { test: 'node --test tests/*.test.mjs' } }, null, 2) + '\n');
console.log(`Source exported: ${target}`);
