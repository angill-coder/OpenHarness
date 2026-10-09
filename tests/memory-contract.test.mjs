import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const read=p=>fs.readFileSync(path.join(root,p),'utf8');

test('capacity policy uses freshness and heat without giant rubric packing',()=>{
 const prompt=read('agents/report-memory-agent.md');
 assert.match(prompt,/1,000 条/);
 assert.match(prompt,/memory-stats.json.*新鲜度.*updatedAt.*热度.*useCount.*lastUsedAt/);
 assert.match(prompt,/不要通过把多条规则塞进一个巨型 Rubric/);
 assert.match(prompt,/新要求先留 L1/);
 const ref=read('skills/report-agent/references/memory-capacity.md');
 assert.match(ref,/runId \+ rubricId/);
 assert.match(ref,/统计更新不推进 MEMORY revision/);
 assert.match(ref,/不能传历史或失败结果/);
 assert.match(read('skills/report-agent/references/loop-orchestration.md'),/每批单维结果核验有效后/);
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
