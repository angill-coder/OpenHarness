import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const manifest = JSON.parse(read('.codebuddy-plugin/plugin.json'));
const mapping = {
  'research-report-agent-v2': 'research-report-team-v2',
  'report-dimension-judge-v2': 'report-team-dimension-judge-v2',
  'report-resolution-judge-v2': 'report-team-resolution-judge-v2',
  'report-evidence-agent-v2': 'report-team-evidence-v2',
  'report-memory-agent-v2': 'report-team-memory-v2',
  'report-writer-v2': 'report-team-writer-v2',
  'report-agent-v2': 'report-agent-v2-expert-team-lead',
};
const translate = text => Object.entries(mapping).reduce((s,[a,b]) => s.replaceAll(a,b), text);
const original = text => Object.entries(mapping).reduce((s,[a,b]) => s.replaceAll(b,a), text);
function filesAt(dir) {
  return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e => e.isDirectory()
    ? filesAt(path.join(dir,e.name)) : [path.join(dir,e.name)]);
}

test('team manifest, settings and six role definitions agree', () => {
  assert.equal(manifest.expertType,'team');
  assert.equal(manifest.plugin,manifest.name);
  assert.equal(JSON.parse(read('package.json')).version,manifest.version);
  assert.equal(JSON.parse(read('settings.json')).agent,manifest.agentName);
  assert.equal(manifest.teamInfo.leadAgent,manifest.agentName);
  assert.deepEqual(manifest.profession,manifest.displayName);
  assert.deepEqual(manifest.defaultInitPrompt,manifest.quickPrompts[0]);
  assert.equal(manifest.tags.length,3);
  assert.equal(manifest.quickPrompts.length,3);
  const ids=[manifest.agentName,...manifest.teamInfo.memberAgents];
  assert.equal(ids.length,6);
  assert.equal(new Set(ids).size,6);
  assert.deepEqual(manifest.members.map(m=>m.id),ids);
  assert.deepEqual(manifest.agents,ids.map(id=>'./agents/'+id+'.md'));
  assert.equal(manifest.members.filter(m=>m.role==='lead').length,1);
  for(const member of manifest.members){
    const content=read('agents/'+member.id+'.md');
    const header=content.split('---')[1];
    assert.match(header,new RegExp('^name: '+member.id+'$','m'));
    for(const key of ['description','displayName','profession','maxTurns']) {
      assert.match(header,new RegExp('^'+key+':','m'));
    }
    assert.doesNotMatch(header,/^(?:tools|disallowedTools):/m);
    if(member.role==='member')assert.match(content,/SendMessage/);
    if(member.id.includes('judge')){
      assert.match(header,/^model: gpt-5.6-sol$/m);
      assert.match(header,/^effort: medium$/m);
    }
  }
});

// Memory prompts intentionally diverge from PR51: explicit user feedback is required.
// Behavioral acceptance cases live in team-scenarios.md; other baseline assets stay fixed.
test('PR51 writing, evidence, judging and scoring invariants unchanged', () => {
  for(const entry of JSON.parse(read('tests/pr51-baseline.json')).entries){
    let body=original(read(translate(entry.file)));
    if(entry.file.endsWith('/workspace-and-delivery.md')){
      const start=body.indexOf('## 目录位置');
      const end=body.indexOf('- **数据**：',start);
      assert.ok(start>=0 && end>start,'directory precedence clarification exists');
      const clarification=body.slice(start,end);
      assert.match(clarification,/1\. 用户明确指定的文件夹。[\s\S]*2\. 用户提供的报告素材文件夹。[\s\S]*3\. 宿主系统/);
      assert.match(clarification,/不覆盖宿主的强制限制或文件访问权限/);
      // Only the approved precedence clarification is new; preserve the PR51 layout.
      body=body.slice(0,start)+body.slice(end);
      body=body.replace(
        '- **报告目录**：用户明确指定报告输出文件夹时直接使用；否则在上述选定位置下创建 `报告/`。',
        '- **报告目录**：用户指定位置优先，否则使用 `素材目录/报告/`，不默认使用 WorkBuddy 会话目录。多处素材没有明确保存位置，或目标不可写时，先确认，不静默换目录。');
    }
    if(entry.start){
      assert.ok(body.includes(entry.start),entry.file);
      body=body.slice(body.indexOf(entry.start));
    }
    assert.equal(createHash('sha256').update(body).digest('hex'),entry.sha256,entry.file);
  }
});

test('every local Markdown reference resolves inside the team package', () => {
  for(const dir of ['agents','skills','resources']){
    for(const file of filesAt(path.join(root,dir)).filter(p=>p.endsWith('.md'))){
      const body=fs.readFileSync(file,'utf8');
      for(const match of body.matchAll(/\]\(([^)\s]+)\)/g)){
        const link=match[1].split('#')[0];
        if(!link||/^[a-z]+:\/\//i.test(link))continue;
        const target=path.resolve(path.dirname(file),decodeURIComponent(link));
        assert.ok(target.startsWith(root+path.sep),file+': outside package '+link);
        assert.ok(fs.existsSync(target),file+': missing '+link);
      }
    }
  }
});

test('avatars exist, are 512px square PNGs and below 500KB', () => {
  for(const avatar of [manifest.avatar,...manifest.members.map(m=>m.avatar)]){
    const bytes=fs.readFileSync(path.join(root,avatar));
    assert.equal(bytes.subarray(1,4).toString(),'PNG',avatar);
    assert.equal(bytes.readUInt32BE(16),512,avatar);
    assert.equal(bytes.readUInt32BE(20),512,avatar);
    assert.ok(bytes.length<=500*1024,avatar);
  }
});

test('team contract removes legacy registration and anonymous resume dispatch', () => {
  const skill=read('skills/research-report-team-v2/SKILL.md');
  const team=read('skills/research-report-team-v2/references/native-agent-contracts.md');
  assert.doesNotMatch(skill,/first-use.md|register_agents.py/);
  assert.equal(fs.existsSync(path.join(root,'resources/workbuddy')),false);
  assert.match(team,/dimensions\[\]/);
  assert.match(team,/6\+2/);
  assert.match(team,/team.assignments/);
  assert.match(team,/assignmentId/);
  for(const file of filesAt(path.join(root,'skills')).filter(p=>p.endsWith('.md'))){
    assert.doesNotMatch(fs.readFileSync(file,'utf8'),/resume=writerAgentId/);
  }
  assert.deepEqual(manifest.teamInfo.memberAgents.filter(id=>id.includes('dimension-judge')),
    ['report-team-dimension-judge-v2']);
});

test('build contains runtime only and every registered resource', () => {
  const py=[process.env.PYTHON,'python3','python'].filter(Boolean).find(p=>
    spawnSync(p,['--version'],{timeout:10000}).status===0);
  assert.ok(py,'Python 3 required for packaging');
  const built=spawnSync(py,[path.join(root,'scripts/build.py')],{encoding:'utf8',timeout:30000});
  assert.equal(built.status,0,built.stderr);
  const archive=built.stdout.trim();
  const listing=spawnSync(py,['-c',
    'import json,sys,zipfile; print(json.dumps(zipfile.ZipFile(sys.argv[1]).namelist()))',archive],
    {encoding:'utf8',timeout:10000});
  assert.equal(listing.status,0,listing.stderr);
  const names=JSON.parse(listing.stdout);
  const allowed=new Set(['.codebuddy-plugin','settings.json','README.md','agents','skills','resources','rubrics','avatars']);
  for(const name of names){
    const [prefix,first]=name.split('/');
    assert.equal(prefix,manifest.name);
    assert.ok(allowed.has(first),name);
  }
  for(const registered of manifest.agents){
    assert.ok(names.includes(manifest.name+'/'+registered.replace(/^\.\//,'')),registered);
  }
  assert.ok(names.includes(manifest.name+'/settings.json'));
  assert.ok(names.includes(manifest.name+'/skills/research-report-team-v2/SKILL.md'));
});
