import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const read=p=>fs.readFileSync(path.join(root,p),'utf8');

test('reflection uses a shared template and separates migration from admission',()=>{
 const prompt=read('agents/report-memory-agent.md');
 const reflection=read('skills/report-agent/references/memory-reflection.md');
 assert.match(prompt,/Capture、Manage、Reflection 写入均读取并遵循/);
 assert.match(prompt,/revision=1/);
 assert.doesNotMatch(prompt,/revision=0|初始为 0/);
 assert.match(prompt,/旧版 0 兼容读取/);
 assert.match(prompt,/执行时读取 \[Reflection 指引\]/);
 assert.match(reflection,/格式整理不改变规则含义/);
 assert.match(reflection,/先核验或保存接收位置，再从 MEMORY 移除/);
 assert.match(reflection,/不把主 Agent|不将主 Agent/);
 assert.match(reflection,/同义|压缩原则/);
 assert.match(reflection,/共用一次 revision 递增/);
 assert.match(reflection,/不宣称全部完成/);
 assert.match(reflection,/仅更新复盘|只更新复盘/);
});

test('capacity policy uses freshness and heat without giant rubric packing',()=>{
 const prompt=read('agents/report-memory-agent.md');
 assert.match(prompt,/1,000 条/);
 assert.match(prompt,/memory-compression.md/);
 assert.match(prompt,/不需要压缩时不读取/);
 assert.doesNotMatch(prompt,/不要通过把多条规则塞进一个巨型 Rubric/);
 const compression=read('skills/report-agent/references/memory-compression.md');
 assert.match(compression,/ID 中的日期和使用次数/);
 assert.match(compression,/不要通过把多条规则塞进一个巨型 Rubric/);
 assert.match(compression,/新要求先留 L1/);
 const ref=read('skills/report-agent/references/memory-capacity.md');
 assert.match(ref,/同 Loop.*只计一次/);
 assert.match(ref,/不改规则、日期、编号或 revision/);
 assert.match(ref,/失败或仍待溯源的 Plan 不计/);
 assert.match(read('skills/report-agent/references/loop-orchestration.md'),/Resolution Plan 校验通过并冻结后/);
});

test('usage acknowledgement precedes dimension judging and stays separate from Plan',()=>{
 const loop=read('skills/report-agent/references/loop-orchestration.md');
 const judge=read('agents/report-resolution-judge.md');
 assert.ok(loop.indexOf('MEMORY_USAGE_COMPLETED')<loop.indexOf('## 5. 按动态维度 Judge'));
 for(const marker of ['MEMORY_USAGE_COMPLETED','MEMORY_USAGE_PENDING']){
  assert.ok(loop.includes(marker));
  assert.ok(judge.includes(marker));
 }
 assert.match(judge,/不再次返回 Plan/);
 assert.match(loop,/Base-only 或无激活规则时跳过/);
 assert.match(read('skills/report-agent/references/memory-compression.md'),/\[容量与统计\]\(memory-capacity.md\) 记录改号/);
});

test('capture emits L2 fields; legacy aliases remain read-compatible only',()=>{
 const prompt=read('agents/report-memory-agent.md');
 const blocks=[...prompt.matchAll(/```json\n([\s\S]*?)\n```/g)].map(m=>JSON.parse(m[1]));
 const capture=blocks.find(b=>b.marker==='MEMORY_CAPTURE_COMPLETED');
 assert.ok(Array.isArray(capture.l2Changes));
 assert.equal(Object.hasOwn(capture,'l2bChanges'),false);
 const cases=JSON.parse(read('tests/memory-cases.json'));
 for(const c of cases)if(c.memory){
  assert.equal(Object.hasOwn(c.memory,'activeL2B'),false);
  if(c.memory.activeL2)assert.ok(Array.isArray(c.memory.activeL2));
 }
 assert.match(prompt,/L2B 与 L2 是同一层级/);
 assert.match(prompt,/只读操作、无变更操作及记忆关闭时，不为改名额外写入/);
 assert.match(prompt,/不回写 L0 原话、L1 历史证据或已有变更记录/);
});

test('memory explains structure and admission before persistence contracts',()=>{
 const p=read('agents/report-memory-agent.md');
 const sections=['## 记忆结构','## 如何决定保存','## 存储','## 操作契约'];
 const indices=sections.map(s=>p.indexOf(s));
 assert.ok(indices.every((n,i)=>n>=0&&(i===0||n>indices[i-1])));
 assert.match(p,/用户明确采纳的建议、明确委托/);
 assert.match(p,/不要求用户亲手重写准则/);
 assert.match(p,/无需先证明长期有效才保存/);
 assert.match(p,/没有固定次数门槛/);
 assert.match(p,/评价写法.*接受交付/);
 assert.doesNotMatch(p,/L1 保留用户提出要求的原话，不写提炼结论/);
 for(const marker of ['MEMORY_RESOLVE_COMPLETED','MEMORY_SOURCE_INSPECTION_COMPLETED','MEMORY_CAPTURE_COMPLETED','MEMORY_CAPTURE_FAILED','MEMORY_MANAGE_COMPLETED','MEMORY_REFLECTION_COMPLETED'])assert.ok(p.includes(marker));
});

test('lead forwards feedback without deciding promotion, preserves negative boundary',()=>{
 const p=read('skills/report-agent/references/memory-orchestration.md');
 assert.match(p,/委派 Capture 不等于新增长期 Rubric/);
 assert.match(p,/单独的记忆委托直接交给 Memory Agent/);
 assert.match(p,/首次写作输入和 Loop 自己产生的评测\/改写不触发 Capture/);
 assert.match(p,/不预先指定 Layer、Scope/);
 assert.match(p,/不限制消息条数/);
 assert.doesNotMatch(p,/最多\s*8\s*条|通常\s*2–6\s*条/);
 for(const file of ['agents/report-team-lead.md','agents/report-memory-agent.md'])assert.match(read(file),/主 Agent 负责转交证据，不负责提炼偏好/);
 assert.match(read('agents/report-memory-agent.md'),/不把主 Agent 的总结当作用户表达/);
 const cases=JSON.parse(read('tests/memory-cases.json'));
 assert.equal(new Set(cases.map(c=>c.id)).size,cases.length);
 assert.ok(cases.filter(c=>c.route).length>=8);
 assert.ok(cases.filter(c=>!c.route).length>=6);
 assert.ok(cases.filter(c=>c.holdout).length>=4);
});
