import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const target = path.resolve(root, '../report-agent-v2-source');
// Export workflow source only. Never include installation metadata or user data.
fs.mkdirSync(target, { recursive: true });
for (const entry of ['agents', 'skills', 'resources', 'rubrics']) {
  fs.cpSync(path.join(root, entry), path.join(target, entry), {
    recursive: true,
    filter: filename => !['.DS_Store', '__pycache__', 'first-use.md'].includes(path.basename(filename))
      && filename !== path.join(root, 'resources/workbuddy')
      && !path.basename(filename).endsWith('-workspace'),
  });
}
// Raw source has no WorkBuddy installation workflow; keep the reporting workflow unchanged.
const skillPath = path.join(target, 'skills/research-report-agent-v2/SKILL.md');
fs.writeFileSync(skillPath, fs.readFileSync(skillPath, 'utf8').replace(/## 首次启用\n[\s\S]*?(?=## 执行步骤)/u, ''));
fs.copyFileSync(path.join(root, 'packaging/source-README.md'), path.join(target, 'README.md'));
console.log(`Source exported: ${target}`);
