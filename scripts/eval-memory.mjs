// Isolated model decision tests. No tools, persisted sessions, MCP or real memory.
// Baseline directory contains agents/, SKILL.md and references/ snapshots.
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const [output,baseline]=process.argv.slice(2);
if(!output||!path.isAbsolute(output))throw Error('An absolute output directory is required');
const cli=process.env.WORKBUDDY_EVAL_CLI;
if(!cli)throw Error('Set WORKBUDDY_EVAL_CLI to the CLI entrypoint');
const cases=JSON.parse(fs.readFileSync(path.join(repo,'tests/memory-cases.json'))).filter(c=>!process.env.CASES||process.env.CASES.split(',').includes(c.id));
const strip=s=>s.replace(/^---\n[\s\S]*?\n---\n/,'');
function prompt(variant,role){
 const r=variant==='baseline'?baseline:repo;
 const refs=variant==='baseline'?'references':'skills/research-report-loop/references';
 const files=role==='curator'?['agents/report-memory-agent.md']:['agents/report-team-lead.md',variant==='baseline'?'SKILL.md':'skills/research-report-loop/SKILL.md',refs+'/memory-orchestration.md'];
 return files.map(f=>strip(fs.readFileSync(path.join(r,f),'utf8'))).join('\n\n');
}
const contract={
 lead:'这是隔离的调度决策测试，不执行写作或文件操作。输入给出了完整对话与虚拟状态，若涉及改稿则视为已经完成并核验。只输出JSON：{"capture":true或false,"handoff":"若委派会传递哪些来源与语境","reason":"依据"}。判断是否因该用户输入委派Capture；不要评价测试预期。',
 curator:'这是隔离的记忆决策测试，视为虚拟状态已读取，不访问真实记忆、不实际写文件，不需SendMessage。lastReflectionAt为今天，不需复盘。只输出JSON：{"l0Changes":[],"l1Changes":[],"l2bChanges":[],"reason":"依据"}。每项写action、content、scope（适用时）、sourceIds和来源性质。列出本次会做的实际增量；无变更用空数组。不要因测试改变判断。'
};
const roles=process.env.ROLES?.split(',')||['lead','curator'];
if(roles.some(r=>!contract[r]))throw Error('ROLES must contain lead or curator');
const jobs=cases.flatMap(c=>['candidate',...(baseline&&c.baseline?['baseline']:[])].flatMap(v=>roles.map(role=>({c,v,role}))));
let failed=false;
async function run({c,v,role}){
 const dir=path.join(output,`eval-${c.id}`,v+'-'+role);fs.mkdirSync(dir,{recursive:true});
 if(fs.existsSync(path.join(dir,'run.json'))){if(!JSON.parse(fs.readFileSync(path.join(dir,'run.json'))).passed)failed=true;return;}
 const sys=prompt(v,role)+'\n\n'+contract[role];
 const input={operation:role==='curator'?'capture':undefined,captureId:c.id,memoryRoot:path.join(dir,'virtual-memory'),task:'研究报告',audience:'董事会',project:'研究项目',conversation:c.conversation,memory:{enabled:true,revision:0,lastReflectionAt:'today',activeL2B:[],...c.memory}};
 fs.writeFileSync(path.join(dir,'prompt.md'),sys);fs.writeFileSync(path.join(dir,'input.json'),JSON.stringify(input,null,2));
 const args=[cli,'-p','--model','hy4-preview-ioa','--effort','medium','--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--setting-sources','','--settings','{"autoMemoryEnabled":false}','--no-session-persistence','--max-turns','1','--output-format','json','--system-prompt',sys,JSON.stringify(input)];
 const start=Date.now(),child=spawn(process.execPath,args,{cwd:dir,env:process.env,stdio:['ignore','pipe','pipe']});let stdout='',stderr='';
 child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');child.stdout.on('data',x=>{stdout+=x;fs.appendFileSync(path.join(dir,'live-stdout.log'),x)});child.stderr.on('data',x=>{stderr+=x;fs.appendFileSync(path.join(dir,'live-stderr.log'),x)});
 const timer=setTimeout(()=>child.kill('SIGTERM'),240000);
 const code=await new Promise(resolve=>{child.on('error',e=>{stderr+=e;resolve(-1)});child.on('close',resolve)});clearTimeout(timer);
 fs.writeFileSync(path.join(dir,'raw.json'),stdout);fs.writeFileSync(path.join(dir,'stderr.txt'),stderr);
 let decision,error,model,trace,usage;
 try {const raw=JSON.parse(stdout),events=Array.isArray(raw)?raw:[raw],r=events.findLast(x=>x.type==='result'),a=events.findLast(x=>x.role==='assistant'&&x.providerData?.model);
 model=a?.providerData?.model;trace=a?.providerData?.traceId;usage=r?.usage;fs.writeFileSync(path.join(dir,'response.txt'),r?.result||'');decision=JSON.parse((r?.result||'').replace(/^```(?:json)?\s*/,'').replace(/\s*```$/,''));}catch(e){error=String(e)}
 const expectations=[];
 const check=(text,passed,evidence)=>expectations.push({text,passed:!!passed,evidence});
 check('requested model completed',code===0&&!error&&model==='hy4-preview-ioa',JSON.stringify({code,error,model,trace}));
 if(decision){
  if(role==='lead')check('Capture routing',decision.capture===c.route,decision.reason);
  else {
   for(const [i,k] of ['l0Changes','l1Changes','l2bChanges'].entries()){
    check(k+' valid array',Array.isArray(decision[k]),JSON.stringify(decision[k]));
    if(c.layers[i]!==null)check(k+' expected presence',Array.isArray(decision[k])&&(decision[k].length>0)===c.layers[i],JSON.stringify(decision[k]));
   }
   if(c.scope)check('L2B scope',decision.l2bChanges?.length>0&&decision.l2bChanges.every(x=>x.scope===c.scope),JSON.stringify(decision.l2bChanges));
   if(c.forbid)check('unendorsed content not in L2B',!JSON.stringify(decision.l2bChanges).includes(c.forbid),JSON.stringify(decision.l2bChanges));
  }
 }
 const passed=expectations.filter(x=>x.passed).length;
 const grading={expectations,summary:{passed,failed:expectations.length-passed,total:expectations.length,pass_rate:passed/expectations.length}};
 const row={id:c.id,variant:v,role,code,error,model,trace,usage,duration_ms:Date.now()-start,passed:grading.summary.failed===0};
 if(!row.passed)failed=true;
 fs.writeFileSync(path.join(dir,'decision.json'),JSON.stringify(decision??null,null,2));fs.writeFileSync(path.join(dir,'grading.json'),JSON.stringify(grading,null,2));fs.writeFileSync(path.join(dir,'run.json'),JSON.stringify(row,null,2));fs.writeFileSync(path.join(dir,'timing.json'),JSON.stringify({total_duration_seconds:row.duration_ms/1000,total_tokens:usage?usage.input_tokens+usage.output_tokens:null}));
 console.log(JSON.stringify(row));
}
let next=0;await Promise.all(Array.from({length:Number(process.env.CONCURRENCY||3)},async()=>{while(next<jobs.length)await run(jobs[next++]);}));
if(failed)process.exitCode=1;
