import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const script = path.join(root, 'resources/report/report_stats.py');
test('report length: Markdown, tables, Unicode paths and version binding', () => {
  const python = [process.env.PYTHON, 'python3', 'python'].filter(Boolean).find(command =>
    spawnSync(command, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'], {timeout:10000}).status === 0);
  assert.ok(python, 'Tests require Python 3.9+');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'report-stats-'));
  try {
    const file = path.join(dir, '中文 报告.md');
    const cases = [
      ['# 标题\n\n**结论**：增长20%。', '标题结论：增长20%。'],
      ['| 指标 | 值 |\n| --- | --- |\n| 用户 | 10 |', '指标值用户10'],
      ['- [证据](https://example.com)\n![图片](x.png)', '证据图片'],
      ['\ufeff# 中英 A1\r\n> 结论\r\n', '中英A1结论'],
      ['', ''],
    ];
    for (const [text, visible] of cases) {
      fs.writeFileSync(file, text);
      const run = spawnSync(python, [script, file, '--max-chars', '10'], {encoding:'utf8', timeout:10000});
      assert.equal(run.status, 0, run.stderr);
      const result = JSON.parse(run.stdout);
      assert.equal(result.visible_chars, [...visible].length);
      assert.equal(result.within_limit, [...visible].length <= 10);
      assert.equal(result.over_by, Math.max(0, [...visible].length - 10));
      assert.equal(result.sha256, createHash('sha256').update(fs.readFileSync(file)).digest('hex'));
      assert.equal(fs.readFileSync(file, 'utf8'), text);
    }
    assert.notEqual(spawnSync(python, [script, file, '--max-chars', '0'], {timeout:10000}).status, 0);
    assert.notEqual(spawnSync(python, [script, path.join(dir,'missing.md')], {timeout:10000}).status, 0);
  } finally { fs.rmSync(dir, {recursive:true, force:true}); }
});
