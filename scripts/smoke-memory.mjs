// Actual file-write smoke test in a caller-supplied disposable directory.
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const root=process.argv[2],cli=process.env.WORKBUDDY_EVAL_CLI;
if(!root||!path.isAbsolute(root)||!cli)throw Error('Pass absolute disposable directory and WORKBUDDY_EVAL_CLI');
if(fs.existsSync(root))throw Error('Use a new directory; never overwrite an existing memory');
fs.mkdirSync(root,{recursive:true});
const memory=path.join(root,'fixture-memory');fs.mkdirSync(memory);
const today=new Date().toLocaleDateString('en-CA');
fs.writeFileSync(path.join(memory,'MEMORY.md'),`revision: 0\nenabled: true\nlastReflectionAt: ${today}\n\n# Active L2B\n\n无。\n`);
const prompt=fs.readFileSync(path.join(repo,'agents/report-memory-agent.md'),'utf8').replace(/^---\n[\s\S]*?\n---\n/,'')+
 '\n\n本次在独立CLI进行真实文件测试，不具备团队SendMessage，请直接返回真实完成/失败结果。唯一允许读写的目录是 '+memory+'；不得访问其他记忆或用户文件。除此之外按上述指令完成操作，不能只描述计划。';
const cases=[
 {id:'article',text:'请从以下文章节选提炼我今后研究报告的写作准则并保存：不要用空话堆砌篇幅；主张应有具体事实支持；要根据读者调整表达。此为我明确委托提炼，不要求逐字复制。'},
 {id:'repeat',captureId:'article',text:'请从以下文章节选提炼我今后研究报告的写作准则并保存：不要用空话堆砌篇幅；主张应有具体事实支持；要根据读者调整表达。此为我明确委托提炼，不要求逐字复制。'},
 {id:'version',text:'用R2作为最佳版本吧。',context:'R2新增了总领判断，改成了IMRD结构；Agent猜测用户认可了这些写法。'}
];
function snapshot(dir){return Object.fromEntries(fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?Object.entries(snapshot(path.join(dir,e.name))).map(([k,v])=>[e.name+'/'+k,v]):[[e.name,fs.readFileSync(path.join(dir,e.name),'utf8')]]));}
for(const c of cases){
 const dir=path.join(root,c.id);fs.mkdirSync(dir);const before=snapshot(memory);
 const input={operation:'capture',captureId:'smoke-'+(c.captureId||c.id),memoryRoot:memory,task:'研究报告',audience:'管理层',project:'测试项目',conversations:[{role:'user',text:c.text}],context:c.context};
 fs.writeFileSync(path.join(dir,'input.json'),JSON.stringify(input,null,2));
 const args=[cli,'-p','--model','hy4-preview-ioa','--effort','medium','--tools','Read,Write,Edit,Glob,Grep','--permission-mode','acceptEdits','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--setting-sources','','--settings','{"autoMemoryEnabled":false}','--no-session-persistence','--max-turns','20','--output-format','json','--system-prompt',prompt,JSON.stringify(input)];
 const start=Date.now(),child=spawn(process.execPath,args,{cwd:memory,env:process.env,stdio:['ignore','pipe','pipe']});let stdout='',stderr='';child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');child.stdout.on('data',x=>stdout+=x);child.stderr.on('data',x=>stderr+=x);
 const timer=setTimeout(()=>child.kill('SIGTERM'),480000);const code=await new Promise(resolve=>{child.on('error',e=>{stderr+=e;resolve(-1)});child.on('close',resolve)});clearTimeout(timer);
 fs.writeFileSync(path.join(dir,'raw.json'),stdout);fs.writeFileSync(path.join(dir,'stderr.txt'),stderr);fs.writeFileSync(path.join(dir,'before.json'),JSON.stringify(before,null,2));fs.writeFileSync(path.join(dir,'after.json'),JSON.stringify(snapshot(memory),null,2));
 console.log(JSON.stringify({case:c.id,code,duration_ms:Date.now()-start,files:Object.keys(snapshot(memory)),unchanged:JSON.stringify(before)===JSON.stringify(snapshot(memory))}));
 if(code!==0)break;
}
