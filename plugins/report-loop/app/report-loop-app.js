"use strict";
const $=id=>document.getElementById(id);
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const inlineMarkdown=value=>esc(value).replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>").replace(/__([^_]+)__/g,"<strong>$1</strong>").replace(/\*([^*]+)\*/g,"<em>$1</em>");
function renderMarkdown(source){
 const lines=String(source??"").replace(/\r\n?/g,"\n").split("\n"),fence=String.fromCharCode(96).repeat(3);let html="",list="",inCode=false,code=[],paragraph=[];
 const closeList=()=>{if(list){html+="</"+list+">";list=""}},flush=()=>{if(paragraph.length){html+="<p>"+inlineMarkdown(paragraph.join(" "))+"</p>";paragraph=[]}},cells=line=>line.trim().replace(/^\||\|$/g,"").split("|").map(x=>x.trim());
 for(let i=0;i<lines.length;i++){const raw=lines[i],trimmed=raw.trim();
  if(trimmed.startsWith(fence)){flush();closeList();if(inCode){html+="<pre><code>"+esc(code.join("\n"))+"</code></pre>";code=[]}inCode=!inCode;continue}if(inCode){code.push(raw);continue}
  const heading=raw.match(/^(#{1,4})\s+(.+)$/);if(heading){flush();closeList();const n=heading[1].length;html+="<h"+n+">"+inlineMarkdown(heading[2])+"</h"+n+">";continue}
  if(trimmed.includes("|")&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[i+1])){flush();closeList();const head=cells(raw),rows=[];i+=2;while(i<lines.length&&lines[i].trim()&&lines[i].includes("|")){rows.push(cells(lines[i++]));}i--;html+='<div class="markdown-table-wrap"><table><thead><tr>'+head.map(x=>"<th>"+inlineMarkdown(x)+"</th>").join("")+"</tr></thead><tbody>"+rows.map(row=>"<tr>"+head.map((_,j)=>"<td>"+inlineMarkdown(row[j]||"")+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>";continue}
  const item=raw.match(/^\s*(?:(\d+)\.|[-*])\s+(.+)$/);if(item){flush();const type=item[1]?"ol":"ul";if(list!==type){closeList();list=type;html+="<"+type+">"}html+="<li>"+inlineMarkdown(item[2])+"</li>";continue}
  if(/^\s*>\s?/.test(raw)){flush();closeList();html+="<blockquote><p>"+inlineMarkdown(raw.replace(/^\s*>\s?/,""))+"</p></blockquote>";continue}if(/^\s*(?:-{3,}|\*{3,})\s*$/.test(raw)){flush();closeList();html+="<hr>";continue}
  if(!trimmed){flush();closeList()}else{closeList();paragraph.push(trimmed)}
 }flush();closeList();if(inCode)html+="<pre><code>"+esc(code.join("\n"))+"</code></pre>";return html;
}
function renderSkillMarkdown(source){
 let text=String(source||"").replace(/\r\n?/g,"\n"),metadata="";
 const frontmatter=text.match(/^---\n([\s\S]*?)\n---\n?/);
 if(frontmatter){
  const rows=frontmatter[1].split("\n").map(line=>{const split=line.indexOf(":");if(split<1)return"";const key=line.slice(0,split).trim(),value=line.slice(split+1).trim().replace(/^["']|["']$/g,"");return"<div><dt>"+esc(key)+"</dt><dd>"+inlineMarkdown(value)+"</dd></div>"}).join("");
  if(rows)metadata='<dl class="skill-frontmatter">'+rows+"</dl>";
  text=text.slice(frontmatter[0].length);
 }
 text=text.replace(/<!--[\s\S]*?-->/g,"").trim();
 return metadata+renderMarkdown(text);
}
function renderStructured(value){if(Array.isArray(value))return '<ol class="structured-list">'+value.map(x=>"<li>"+renderStructured(x)+"</li>").join("")+"</ol>";if(value&&typeof value==="object")return '<dl class="structured-tree">'+Object.entries(value).map(([key,item])=>"<div><dt>"+esc(key)+"</dt><dd>"+renderStructured(item)+"</dd></div>").join("")+"</dl>";const type=value===null?"null":typeof value;return '<span class="structured-value '+type+'">'+esc(value===null?"null":value)+"</span>";}
function formatReportDocuments(){$("reportTree").querySelectorAll(".report-content").forEach(pre=>{const article=document.createElement("article");article.className="markdown-doc report-document";article.innerHTML=renderMarkdown(pre.textContent||"");pre.replaceWith(article)})}
function formatDatasetPreview(){const p=S.run?.dataset_preview||S.preview;if(!p)return;$("datasetFileList").querySelectorAll(":scope > div").forEach(item=>item.classList.add("dataset-preview-file"));$("structuredView").innerHTML=renderStructured(p.structured_data)}
function formatSkillDocuments(){const x=template();if(!x)return;$("skillDoc").innerHTML=renderSkillMarkdown(x.skill);$("instructionDoc").innerHTML=renderSkillMarkdown(x.instruction)}
const score=v=>Number.isFinite(Number(v))?Number(v).toFixed(2):"—";
const time=v=>v?new Date(v*1000).toLocaleString("zh-CN",{hour12:false}):"—";
const last=r=>(r?.revisions||[]).at(-1)||null;
const accepted=r=>(r?.revisions||[]).find(v=>v.version===r.current_version)||null;
const S={datasets:[],templates:[],runs:[],run:null,preview:null,key:"",busy:false,runningRole:null,loopRunning:false,version:null,traceVersion:null,versionMode:"one",compareVersions:[],compareTraces:new Set(),config:null,runnerModel:"",judgeModel:""};
async function api(path,options={}){
  const res=await fetch(path,{headers:{"Content-Type":"application/json"},...options});
  const body=await res.json().catch(()=>({}));
  if(!res.ok)throw new Error(body.error||("HTTP "+res.status));
  return body;
}
function error(e){$("datasetLoadStatus").textContent=e?.message||String(e);$("datasetLoadStatus").classList.add("is-error")}
function dataset(){return S.datasets.find(x=>x.name===$("datasetSelect").value)}
function template(){return S.templates.find(x=>x.id===$("skillTemplate").value)}
function key(){return $("datasetSelect").value+"|"+$("caseSelect").value}
function isImported(){return !!S.run&&S.key===key()}
function fillCases(value){
  $("caseSelect").innerHTML=(dataset()?.cases||[]).map(x=>'<option value="'+esc(x.case_id)+'">'+esc(x.topic||x.case_id)+'</option>').join("")||'<option value="">无可用 Case</option>';
  if(value)$("caseSelect").value=value;
}
function selectors(){
  const data=$("datasetSelect").value,caseId=$("caseSelect").value,skill=$("skillTemplate").value;
  $("datasetSelect").innerHTML=S.datasets.map(x=>'<option value="'+esc(x.name)+'">'+esc(x.name)+'</option>').join("")||'<option value="">未发现数据集</option>';
  if(S.datasets.some(x=>x.name===data))$("datasetSelect").value=data;
  fillCases(caseId);
  $("skillTemplate").innerHTML=S.templates.map(x=>'<option value="'+esc(x.id)+'">'+esc(x.label||x.id)+'</option>').join("")||'<option value="">未发现 Skill</option>';
  if(S.templates.some(x=>x.id===skill))$("skillTemplate").value=skill;
  $("skillOpen").disabled=!template();
}
function importState(){
  const ready=isImported();
  $("datasetLoad").querySelector("span").textContent=ready?"查看dataset":"导入dataset";
  if(!$("datasetLoadStatus").classList.contains("is-error"))$("datasetLoadStatus").textContent=ready?"已导入 "+S.run.data_id+" / "+S.run.topic:"首次点击仅导入数据；成功后再次点击查看资料包。";
}
const roles={
 runner:{title:"Runner",meta:"报告生成与迭代",action:"生成报告V1",note:"首次读取 Case 数据生成报告；后续读取 Judge 结果迭代下一版本。"},
 judge:{title:"Judge",meta:"报告评测",action:"评测当前报告",note:"输出总分、六个真实维度明细与评分理由。"}
};
function step(id){
 const a=S.run?.actions||{},v=last(S.run);
 const reportImported=!!v?.report_sha256&&!!String(v?.report_text||"").trim()&&["report_ready","judged"].includes(v?.status);
 if(id==="runner"){
  if(!S.run)return{on:false,done:false,text:"Case 数据未导入",action:"生成报告V1"};
  if(!v)return{on:!!a.generate?.enabled,done:true,text:"Case 数据已成功导入",action:"生成报告V1"};
  const judged=v.status==="judged",stopped=!!S.run.stop_state?.stopped;
  return{on:!!a.generate?.enabled,done:judged,text:stopped?(S.run.stop_state.reason||"迭代已停止"):judged?"Judge "+v.version+" 结果已成功导入":"等待 Judge 评测结果",action:"生成报告V"+((S.run.revisions||[]).length+1)};
 }
 if(id==="judge")return{on:!!a.judge?.enabled,done:reportImported,text:reportImported?"报告 "+v.version+" 已成功导入":"生成报告未导入"};
 return{on:false,done:false,text:""};
}
function roleModelField(id){
 const models=(id==="judge"?S.config?.evaluation_models:S.config?.models)||[];
 const stateKey=id+"Model",fallback=id==="judge"?S.config?.judge_wb_model:S.config?.model;
 const desired=S[stateKey]||fallback||models[0]||"";
 S[stateKey]=desired;
 return '<div class="field"><label>'+roles[id].title+' 模型</label><select class="control" data-model-role="'+id+'" '+(S.busy?"disabled":"")+'>'+models.map(model=>'<option value="'+esc(model)+'" '+(model===desired?"selected":"")+'>'+esc(model)+'</option>').join("")+'</select></div>';
}
function backendLabel(b){return b==="workbuddy"?"WorkBuddy CLI":b==="codex"?"Codex CLI":"bianxie API"}
function backendField(role){
 const backends=[{v:"workbuddy",t:"WorkBuddy CLI"},{v:"codex",t:"Codex CLI"},{v:"api",t:"bianxie API"}];
 const desired=S[role+"Backend"]||"workbuddy";
 S[role+"Backend"]=desired;
 return '<div class="field"><label>调用方式</label><select class="control" data-backend-role="'+role+'" '+(S.busy?"disabled":"")+'>'+backends.map(b=>'<option value="'+b.v+'" '+(b.v===desired?"selected":"")+'>'+esc(b.t)+'</option>').join("")+'</select></div>';
}
function backendModelField(role){
 const backend=S[role+"Backend"]||"workbuddy";
 const dis=S.busy?"disabled":"";
 if(backend==="workbuddy"){
  const models=S.config?.models||[];
  const desired=S[role+"Model"]||S.config?.model||models[0]||"";
  S[role+"Model"]=desired;
  let html='<div class="field"><label>WorkBuddy 模型</label><select class="control" data-model-role="'+role+'" '+dis+'>'+models.map(m=>'<option value="'+esc(m)+'" '+(m===desired?"selected":"")+'>'+esc(m)+'</option>').join("")+'</select></div>';
  if(S.config?.wb_cli_ready===false)html+='<div class="note warn">'+(S.config?.wb_cli_error||"WorkBuddy CLI 不可用")+'</div>';
  return html;
 }
 if(backend==="codex"){
  const models=S.config?.codex_models||[];
  const desired=S[role+"CodexModel"]||S.config?.codex_model_default||models[0]||"";
  S[role+"CodexModel"]=desired;
  let html='<div class="field"><label>Codex 模型</label><select class="control" data-model-role="'+role+'Codex" '+dis+'>'+models.map(m=>'<option value="'+esc(m)+'" '+(m===desired?"selected":"")+'>'+esc(m)+'</option>').join("")+'</select></div>';
  const efforts=S.config?.codex_reasoning_efforts||[];
  const red=S[role+"CodexReasoning"]||S.config?.codex_reasoning_effort_default||efforts[0]||"";
  S[role+"CodexReasoning"]=red;
  const opts=efforts.length?efforts.map(e=>'<option value="'+esc(e)+'" '+(e===red?"selected":"")+'>'+esc(e)+'</option>').join(""):'<option value="">—</option>';
  html+='<div class="field"><label>Codex 推理强度</label><select class="control" data-reasoning-role="'+role+'" '+dis+'>'+opts+'</select></div>';
  if(S.config?.codex_cli_ready===false)html+='<div class="note warn">'+(S.config?.codex_cli_error||"Codex CLI 不可用")+'</div>';
  return html;
 }
 const models=S.config?.api_models||[];
 const desired=S[role+"ApiModel"]||S.config?.api_model_default||models[0]||"";
 S[role+"ApiModel"]=desired;
 return '<div class="field"><label>API 模型</label><select class="control" data-model-role="'+role+'Api" '+dis+'>'+models.map(m=>'<option value="'+esc(m)+'" '+(m===desired?"selected":"")+'>'+esc(m)+'</option>').join("")+'</select></div>';
}
function pipeline(){
function evolution(){
 const revisions=S.run?.revisions||[];
 return '<div class="runner-evolution"><div class="runner-evolution-track"><span class="runner-evolution-label">版本演进：</span>'+(revisions.length?revisions.map(v=>'<span class="runner-version '+(v.decision==="rejected"?"rejected":v.decision==="accepted"||v.version===S.run.current_version?"accepted":"pending")+'">'+esc(v.version)+'</span>').join('<i>→</i>'):'<small>尚未生成报告</small>')+'</div></div>';
}
 $("pipeline").innerHTML=Object.entries(roles).map(([id,r])=>{const s=step(id),running=S.runningRole===id;return '<section class="card control-card"><div class="card-head"><div class="role-title"><i class="role-dot"></i><h2>'+r.title+'</h2></div><small>'+r.meta+'</small></div><div class="card-body pipeline-fields">'+(id==="runner"?evolution():"")+(id==="runner"?backendField("runner")+backendModelField("runner"):(id==="judge"?backendField("judge")+backendModelField("judge"):roleModelField(id)))+'<p class="pipeline-note">'+esc(r.note)+'</p><div class="pipeline-actions has-status"><div class="pipeline-import-status'+(s.done?"":" is-missing")+'"><i>'+(s.done?"✓":"—")+'</i><span>'+esc(s.text)+'</span></div><button class="step-button" data-role="'+id+'" '+(s.on&&!S.busy?"":"disabled")+(running?' aria-busy="true"':"")+'>'+(running?"任务运行中…":s.action||r.action)+'</button></div></div></section>'}).join("");
}
function runJudgment(r){return accepted(r)?.judgment||last(r)?.judgment||{}}
function redlineCount(judgment){
  if(!judgment||typeof judgment!=="object")return 0;
  if(Array.isArray(judgment.redline_checks))return judgment.redline_checks.length;
  const checks=judgment.checks||{};
  const rubric=S.run?.rubric;
  if(!rubric)return 0;
  let n=0;
  for(const dim of (rubric.dimensions||[]))for(const c of (dim.checks||[]))if(c.redline&&checks[c.id]==="miss")n++;
  return n;
}
function runs(){
 const openId=S.run?.id;
 $("sessionSelect").innerHTML=S.runs.map(r=>'<option value="'+esc(r.id)+'" '+(r.id===openId?"selected":"")+'>'+esc(r.id)+" · "+esc(r.topic)+'</option>').join("")||'<option value="">暂无 Report Run</option>';
 if(!S.runs.length){$("latestBody").innerHTML='<tr><td colspan="14">暂无可用的 Report Run。</td></tr>';return;}
 $("latestBody").innerHTML=S.runs.map(r=>{const j=runJudgment(r),d=j.dimensions||{},rc=redlineCount(j),sel=r.id===openId;return '<tr data-run="'+esc(r.id)+'"'+(sel?' class="selected"':"")+'><td><button type="button" class="latest-pick'+(sel?' selected" aria-pressed="true':'" aria-pressed="false"')+'">'+(sel?'✓ 已选':'选为当前')+'</button></td><td>'+esc(r.id)+'</td><td>'+esc(r.topic)+'</td><td>'+esc(r.current_version||r.latest_version||"—")+'</td><td>'+time(r.created_at)+'</td><td>'+(r.stop_state?.stopped?time(r.updated_at):"—")+'</td><td>'+score(j.overall)+'</td><td class="redline-value'+(rc>0?' has-redline':'')+'">'+rc+'</td><td>'+score(d.traceability)+'</td><td>'+score(d.structure)+'</td><td>'+score(d.narrative)+'</td><td>'+score(d.insight)+'</td><td>'+score(d.coverage)+'</td><td>'+score(d.expression)+'</td></tr>'}).join("");
}
const EVALUATION_DIMENSIONS=[
 ["traceability","可回溯性"],["structure","结构"],["narrative","逻辑与故事线"],
 ["insight","提炼与洞察"],["coverage","覆盖度"],["expression","表达"]
];
function rubricRows(version,name,interactive=false){
 const definition=(S.run?.rubric?.dimensions||[]).find(item=>item.name===name)||{};
 const checks=version.judgment?.checks||{},scores=version.judgment?.check_scores||{},reasons=version.judgment?.reasoning||{};
 const label=value=>value==="met"?"达标":value==="partial"?"部分":value==="miss"?"缺失":"—";
 return (definition.checks||[]).map(item=>{const v=checks[item.id],n=scores[item.id],reason=reasons[item.id]||"尚无评分理由",num=n!==undefined&&n!==null?(" ("+Number(n).toFixed(1)+")"):"",reasonView=interactive&&reasons[item.id]?'<button type="button" class="rubric-evidence-trigger" data-rubric-version="'+esc(version.version)+'" data-rubric-check="'+esc(item.id)+'" aria-pressed="false" title="点击定位报告中的对应文段">'+esc(reason)+'</button>':'<p>'+esc(reason)+'</p>';return '<div class="rubric-item"><span>'+esc(item.label||item.id)+'</span><b class="'+(v==="miss"?"fail":"")+'">'+label(v)+num+'</b>'+reasonView+'</div>'}).join("")||'<div class="rubric-item"><span>尚未评测</span><b>—</b><p>Judge 完成后显示真实评分理由</p></div>';
}
function dimensionGroups(version,interactive=false){
 const dimensions=version.judgment?.dimensions||{};
 return EVALUATION_DIMENSIONS.map(([name,label])=>'<section class="dimension-group"><div class="dimension-group-head"><span>'+label+'</span><b>'+score(dimensions[name])+'</b></div><div class="rubric-list"><div class="rubric-list-head"><span>Rubric</span><span>结果</span><span>Judge 理由（点击定位原文）</span></div>'+rubricRows(version,name,interactive)+'</div></section>').join("");
}
function tracePanel(version){
  return '<aside class="trace-pane"><div class="expanded-pane-head"><h3>报告生成链路</h3></div><div class="trace-run-head">'+esc(version.version)+' · 运行轨迹（runner/conversation.md）</div><div class="trace-steps" data-genchain="'+esc(version.version)+'"><div class="gen-loading">加载生成链路…</div></div></aside>';
}
function reportDetail(version){
 const judgment=version.judgment||{},traceOpen=S.traceVersion===version.version;
 const redlines=redlineCount(judgment);
 return '<div class="version-detail on rich-report-detail report-detail-panel"><div class="single-report-layout'+(traceOpen?" has-trace":"")+'"><aside class="evaluation-pane"><div class="expanded-pane-head"><h3>评测表现</h3></div><div class="evaluation-scroll"><div class="score-cards"><div class="score-card total"><small>评测总分 / 5</small><strong>'+score(judgment.overall)+'</strong></div><div class="score-card redline"><small>红线数量</small><strong>'+redlines+'</strong></div></div><div class="dimension-groups">'+dimensionGroups(version)+'</div></div></aside><section class="report-pane"><div class="expanded-pane-head report-pane-head"><h3>报告全文</h3><button class="trace-button" type="button" data-trace-version="'+esc(version.version)+'">'+(traceOpen?"收起生成链路":"生成链路")+'</button></div><article class="report-document">'+renderMarkdown(version.report_text||"")+'</article></section>'+(traceOpen?tracePanel(version):"")+'</div></div>';
}
function compareDefaults(list){
 if(list.length<2)return list.map(v=>v.version);
 return [list[0].version,list.at(-1).version];
}
function compareIds(list){
 const valid=new Set(list.map(v=>v.version));
 let ids=S.compareVersions.filter(id=>valid.has(id)).slice(0,3);
 if(ids.length<2)ids=compareDefaults(list);
 S.compareVersions=ids;
 return ids;
}
function versionModels(version){
 const attempts=version.trace?.case?.attempts||[];
 const attemptModel=attempts.at(-1)?.configured_model;
 const apiModel=version.trace?.case?.model;
 return{runner:attemptModel||apiModel||"—",judge:version.judgment?.model||"—"};
}
function compareTrace(version){
  return '<div class="compare-trace-view"><div class="compare-trace-run-head">真实运行记录 · '+esc(version.version)+'</div><div class="compare-trace-steps" data-genchain="'+esc(version.version)+'"><div class="gen-loading">加载生成链路…</div></div></div>';
}
const REVISION_PROMPT_LABELS={task:"本轮改写目标",base_version:"基线版本",base_report:"基线报告",evidence:"可用证据",failure_report:"待修复问题",preserve_checks:"必须保持的通过项",redline_constraints:"红线约束",rejected_history:"被拒版本与差异",original_turns:"原始用户输入",requirement:"补充要求",adoption_gate:"采纳门禁",dimension_delta:"维度变化",rejected_diff:"被拒修改 Diff",version:"版本",parent_version:"父版本",overall:"总分",failures:"失败项"};
const REVISION_PROMPT_FOLDS=new Set(["base_report","evidence","rejected_history","original_turns","rejected_diff"]);
function revisionPromptLabel(key){return REVISION_PROMPT_LABELS[key]||String(key).replaceAll("_"," ")}
function revisionPromptMeta(value){if(Array.isArray(value))return value.length+" 项";if(value&&typeof value==="object")return Object.keys(value).length+" 项";if(typeof value==="string"&&value.length>120)return value.length.toLocaleString()+" 字符";return ""}
function revisionPromptValue(value,key,depth=0){
 if(value==null||value==="")return '<span class="revision-prompt-empty">无</span>';
 if(typeof value==="boolean")return '<span class="revision-prompt-status '+(value?"yes":"no")+'">'+(value?"是":"否")+'</span>';
 if(typeof value==="number")return '<span class="revision-prompt-number">'+esc(value)+'</span>';
 if(typeof value==="string"){if(key==="base_report")return '<article class="revision-prompt-markdown">'+renderMarkdown(value)+'</article>';if(key==="rejected_diff")return '<pre class="revision-prompt-diff">'+esc(value)+'</pre>';return '<div class="revision-prompt-text">'+renderMarkdown(value)+'</div>'}
 if(Array.isArray(value)){if(!value.length)return '<span class="revision-prompt-empty">无</span>';return '<ol class="revision-prompt-list">'+value.map((item,index)=>'<li><span class="revision-prompt-index">'+(index+1)+'</span><div>'+revisionPromptValue(item,key,depth+1)+'</div></li>').join("")+'</ol>'}
 const entries=Object.entries(value);if(!entries.length)return '<span class="revision-prompt-empty">无</span>';return '<dl class="revision-prompt-kv depth-'+Math.min(depth,3)+'">'+entries.map(([childKey,childValue])=>'<div><dt>'+esc(revisionPromptLabel(childKey))+'</dt><dd>'+revisionPromptValue(childValue,childKey,depth+1)+'</dd></div>').join("")+'</dl>'
}
function parseRevisionPrompt(content,version){
 const rank=Number(String(version||"").match(/\d+/)?.[0]||0);
 if(rank<2||typeof content!=="string"||!content.trim().startsWith("{"))return null;
 try{const value=JSON.parse(content);return value&&typeof value==="object"&&!Array.isArray(value)&&value.task&&value.base_version?value:null}catch(e){return null}
}
function renderRevisionPrompt(prompt){
 const preferred=["task","failure_report","preserve_checks","redline_constraints","rejected_history","evidence","base_report","original_turns","requirement"],keys=[...preferred.filter(key=>Object.hasOwn(prompt,key)),...Object.keys(prompt).filter(key=>key!=="base_version"&&!preferred.includes(key))];
 return '<div class="revision-prompt"><header class="revision-prompt-head"><div><span>报告迭代 Prompt</span><strong>结构化改写指令</strong></div><span class="revision-prompt-base">基于 '+esc(prompt.base_version)+'</span></header><div class="revision-prompt-sections">'+keys.map(key=>{const value=prompt[key],fold=REVISION_PROMPT_FOLDS.has(key),meta=revisionPromptMeta(value),heading='<span>'+esc(revisionPromptLabel(key))+'</span>'+(meta?'<small>'+esc(meta)+'</small>':"");return fold?'<details class="revision-prompt-section revision-prompt-fold"><summary>'+heading+'</summary><div class="revision-prompt-body">'+revisionPromptValue(value,key)+'</div></details>':'<section class="revision-prompt-section"><h4>'+heading+'</h4><div class="revision-prompt-body">'+revisionPromptValue(value,key)+'</div></section>'}).join("")+'</div></div>';
}
function renderGenerationChain(chain,version){
  if(!chain||!chain.turns||!chain.turns.length)return '<div class="gen-empty">该版本未记录可解析的对话轨迹。</div>';
  return '<div class="gen-chain">'+chain.turns.map(turn=>renderGenTurn(turn,version)).join("")+'</div>';
}
function toolArgSummary(tool){
  if(tool.args&&tool.args.trim())return tool.args.trim();
  const s=tool.input||"";
  try{
    const j=JSON.parse(s);
    if(j&&typeof j==="object"&&!Array.isArray(j)){
      const prefer=["file_path","path","pattern","skill","command","query","file"];
      for(const k of prefer){if(j[k]!=null)return k+"="+String(j[k]);}
      const keys=Object.keys(j);
      if(keys.length)return keys[0]+"="+String(j[keys[0]]);
    }
  }catch(e){}
  return "";
}
function unwrapToolPayload(s){
  if(typeof s!=="string"||!s.trim())return s;
  try{
    const j=JSON.parse(s);
    if(Array.isArray(j)){
      const texts=j.map(x=>(x&&typeof x==="object"&&typeof x.text==="string")?x.text:null).filter(t=>t!==null);
      if(texts.length===j.length&&texts.length)return texts.join("\n");
    }else if(j&&typeof j==="object"&&typeof j.text==="string"){
      return j.text;
    }
  }catch(e){}
  return s;
}
function genPre(content){
  if(content==null)return "";
  const disp=unwrapToolPayload(content);
  const safe=esc(disp);
  if(disp.length<=700)return '<pre class="gen-pre">'+safe+'</pre>';
  return '<div class="gen-pre-wrap"><pre class="gen-pre gen-pre-long">'+safe+'</pre><button type="button" class="gen-pre-toggle">展开全部</button></div>';
}
function renderGenTool(tool){
  const metaParts=[];
  if(tool.status)metaParts.push(tool.status);
  if(tool.duration)metaParts.push(tool.duration);
  const meta=metaParts.join(" · ");
  const arg=toolArgSummary(tool);
  let head='<span class="gen-tool-name">'+esc(tool.name)+'</span>';
  if(meta)head+='<span class="gen-tool-meta">'+esc(meta)+'</span>';
  if(arg)head+='<span class="gen-tool-args" title="'+esc(arg)+'">'+esc(arg)+'</span>';
  let body="";
  if(tool.input)body+='<details class="gen-detail"><summary>输入</summary>'+genPre(tool.input)+'</details>';
  if(tool.output)body+='<details class="gen-detail"><summary>输出</summary>'+genPre(tool.output)+'</details>';
  if(!body)body='<div class="gen-tool-empty">（无输入/输出记录）</div>';
  return '<details class="gen-tool" open><summary class="gen-tool-head">'+head+'</summary><div class="gen-tool-body">'+body+'</div></details>';
}
function renderGenTurn(t,version){
  if(t.role==="user"){const prompt=parseRevisionPrompt(t.content,version);return '<div class="gen-turn gen-turn-user"><div class="gen-who">用户</div><div class="gen-bubble gen-bubble-user">'+(prompt?renderRevisionPrompt(prompt):renderMarkdown(t.content||""))+'</div></div>'}
  const tools=(t.tools||[]).map(renderGenTool).join("");
  const thinking=t.thinking?'<details class="gen-thinking"><summary>深度思考</summary><div class="gen-thinking-body">'+esc(t.thinking)+'</div></details>':"";
  const bubble=t.content?'<div class="gen-bubble gen-bubble-agent">'+renderMarkdown(t.content)+'</div>':"";
  return '<div class="gen-turn gen-turn-agent"><div class="gen-who">Agent</div>'+bubble+thinking+(tools?'<div class="gen-chain-exec">'+tools+'</div>':"")+'</div>';
}
document.addEventListener("click",function(e){
  const btn=e.target.closest&&e.target.closest(".gen-pre-toggle");
  if(btn){
    const wrap=btn.parentElement;
    const pre=wrap?wrap.querySelector(".gen-pre"):null;
    if(pre){const ex=pre.classList.toggle("gen-pre-expanded");btn.textContent=ex?"收起":"展开全部";}
  }
});
function loadGenerationChains(){document.querySelectorAll("[data-genchain]").forEach(el=>loadGenerationChain(el.dataset.genchain,el))}
async function loadGenerationChain(version,el){
  try{
    const data=await api("/api/report-loop/generation-chain?id="+encodeURIComponent(S.run.id)+"&version="+encodeURIComponent(version));
    if(data.found&&data.chain&&data.chain.turns&&data.chain.turns.length){el.innerHTML=renderGenerationChain(data.chain,version);return}
    const payload=data.fallback_trace||{};
    el.innerHTML='<div class="gen-empty">'+(data.note||"该版本未生成本地运行轨迹，以下为原始 trace：")+'</div><pre class="trace-copy on">'+esc(JSON.stringify(payload,null,2))+'</pre>';
  }catch(e){el.innerHTML='<div class="gen-empty">生成链路加载失败：'+esc(e.message||String(e))+'</div>'}
}
function compareVersionCard(version){
 const judgment=version.judgment||{},dimensions=judgment.dimensions||{},redlines=redlineCount(judgment),models=versionModels(version),traceOpen=S.compareTraces.has(version.version);
 return '<article class="report-compare-card report-version-panel"><header class="report-panel-head"><div><span>'+esc(version.version)+'</span><small>报告生成模型：'+esc(models.runner)+'</small><small>Judge 模型：'+esc(models.judge)+'</small></div></header><div class="report-score-strip" style="--report-score-cols:'+(EVALUATION_DIMENSIONS.length+2)+'"><div><small>总分</small><b class="'+(Number(judgment.overall)>=4?"score-good":"score-warn")+'">'+score(judgment.overall)+'</b></div><div class="redline-cell"><small>红线</small><b>'+redlines+'</b></div>'+EVALUATION_DIMENSIONS.map(([name,label])=>'<div><small>'+esc(label)+'</small><b>'+score(dimensions[name])+'</b></div>').join("")+'</div><div class="report-panel-body"><aside class="compare-evaluation-pane '+(traceOpen?"showing-trace":"showing-evaluation")+'"><div class="compare-side-head"><strong>'+(traceOpen?"报告生成链路":"评测表现")+'</strong><button type="button" data-compare-trace="'+esc(version.version)+'" aria-pressed="'+traceOpen+'"><i>'+(traceOpen?"←":"↗")+'</i><span>'+(traceOpen?"返回评测表现":"报告生成链路")+'</span></button></div>'+(traceOpen?compareTrace(version):'<div class="compare-evaluation-scroll"><div class="score-cards"><div class="score-card total"><small>评测总分 / 5</small><strong>'+score(judgment.overall)+'</strong></div><div class="score-card redline"><small>红线数量</small><strong>'+redlines+'</strong></div></div><div class="dimension-groups">'+dimensionGroups(version,true)+'</div></div>')+'</aside><section class="compare-report-pane"><div class="compare-report-head">报告全文</div><article class="compare-report-document">'+renderMarkdown(version.report_text||"")+'</article></section></div></article>';
}
function versionCompare(list){
 if(list.length<2)return '<div class="empty">当前会话只有 '+list.length+' 个报告版本，至少生成并评测两个版本后才能对比。</div>';
 const ids=compareIds(list),versions=ids.map(id=>list.find(v=>v.version===id)).filter(Boolean);
 const selectors=ids.map((id,index)=>'<label><span>版本 '+(index+1)+'</span><select class="compare-version-select" data-compare-index="'+index+'">'+list.map(v=>'<option value="'+esc(v.version)+'" '+(v.version===id?"selected":"")+' '+(ids.includes(v.version)&&v.version!==id?"disabled":"")+'>'+esc(v.version)+'</option>').join("")+'</select>'+(ids.length>2?'<button type="button" data-remove-compare="'+index+'">删除</button>':"")+'</label>').join("");
 const add=ids.length<3&&list.length>ids.length?'<button type="button" class="add-version-button" data-add-compare>＋ 添加版本</button>':"";
 return '<div class="report-version-compare-toolbar"><div><b>多版本对比</b><small>选择 2–3 个真实报告版本，横向对照 Judge 结果与报告全文</small><div class="report-diff-summary" data-report-diff-summary><span class="modified">修改</span><span class="added">新增</span><span class="deleted">删除</span><em>点击差异可同步定位其他版本</em></div></div><div class="report-version-compare-picks">'+selectors+add+'</div></div><div class="report-version-compare-wrap"><div class="report-version-compare report-version-panels" style="--report-version-cols:'+versions.length+'">'+versions.map(compareVersionCard).join("")+'</div></div>';
}
const REPORT_DIFF_SELECTOR="h1,h2,h3,h4,p,li,blockquote,pre,th,td";
function reportDiffBlocks(documentNode){
 const normalize=value=>String(value||"").replace(/\s+/g," ").trim();
 return [...documentNode.querySelectorAll(REPORT_DIFF_SELECTOR)].map((node,index)=>({node,index,key:normalize(node.textContent),tag:node.tagName})).filter(item=>item.key);
}
function reportDiffSimilarity(left,right){
 if(left.tag!==right.tag)return 0;
 const grams=value=>{const compact=String(value||"").toLowerCase().replace(/\s/g,"");if(compact.length<2)return new Set([compact]);const result=new Set();for(let i=0;i<compact.length-1;i++)result.add(compact.slice(i,i+2));return result};
 const a=grams(left.key),b=grams(right.key);let overlap=0;a.forEach(token=>{if(b.has(token))overlap++});return a.size+b.size?2*overlap/(a.size+b.size):0;
}
function reportDiffPairs(oldItems,newItems){
 const candidates=[];oldItems.forEach((oldItem,oldIndex)=>newItems.forEach((newItem,newIndex)=>{const similarity=reportDiffSimilarity(oldItem,newItem);if(similarity>=0.3)candidates.push({oldIndex,newIndex,similarity})}));
 candidates.sort((a,b)=>b.similarity-a.similarity||Math.abs(a.oldIndex-a.newIndex)-Math.abs(b.oldIndex-b.newIndex));
 const usedOld=new Set(),usedNew=new Set(),chosen=[];candidates.forEach(candidate=>{if(!usedOld.has(candidate.oldIndex)&&!usedNew.has(candidate.newIndex)){usedOld.add(candidate.oldIndex);usedNew.add(candidate.newIndex);chosen.push(candidate)}});return{chosen,usedOld,usedNew};
}
function markReportDiff(item,kind,slot){
 const priority={deleted:1,added:2,modified:3},current=item.node.dataset.reportDiffKind;if(current&&priority[current]>priority[kind])return;
 item.node.classList.remove("report-diff-modified","report-diff-added","report-diff-deleted");item.node.classList.add("report-diff-block","report-diff-"+kind);item.node.dataset.reportDiffKind=kind;item.node.dataset.reportDiffSlot=slot;
}
function compareReportDocuments(oldDocument,newDocument){
 const oldBlocks=reportDiffBlocks(oldDocument),newBlocks=reportDiffBlocks(newDocument),table=Array.from({length:oldBlocks.length+1},()=>new Uint16Array(newBlocks.length+1));
 for(let i=oldBlocks.length-1;i>=0;i--)for(let j=newBlocks.length-1;j>=0;j--)table[i][j]=oldBlocks[i].key===newBlocks[j].key&&oldBlocks[i].tag===newBlocks[j].tag?table[i+1][j+1]+1:Math.max(table[i+1][j],table[i][j+1]);
 const matches=[];let i=0,j=0;while(i<oldBlocks.length&&j<newBlocks.length){if(oldBlocks[i].key===newBlocks[j].key&&oldBlocks[i].tag===newBlocks[j].tag){matches.push([i,j]);i++;j++}else if(table[i+1][j]>=table[i][j+1])i++;else j++}
 const anchors=[[-1,-1],...matches,[oldBlocks.length,newBlocks.length]];
 for(let segment=0;segment<anchors.length-1;segment++){
  const [oldLeft,newLeft]=anchors[segment],[oldRight,newRight]=anchors[segment+1],oldSegment=oldBlocks.slice(oldLeft+1,oldRight),newSegment=newBlocks.slice(newLeft+1,newRight),pairs=reportDiffPairs(oldSegment,newSegment);
  pairs.chosen.forEach(pair=>{const oldItem=oldSegment[pair.oldIndex],newItem=newSegment[pair.newIndex],slot="report-change-"+oldItem.index;markReportDiff(oldItem,"modified",slot);markReportDiff(newItem,"modified",slot)});
  oldSegment.forEach((item,index)=>{if(!pairs.usedOld.has(index))markReportDiff(item,"deleted","report-deleted-"+item.index)});
  newSegment.forEach((item,index)=>{if(!pairs.usedNew.has(index))markReportDiff(item,"added","report-added-"+segment+"-"+item.index)});
 }
}
function applyReportDiff(){
 const root=$("reportCompareTree"),cards=[...root.querySelectorAll(".report-version-panel")];if(S.versionMode!=="many"||cards.length<2)return;
 const entries=cards.map(card=>{const label=card.querySelector(".report-panel-head span")?.textContent||"",rank=Number((label.match(/\d+/)||[-1])[0]);return{card,rank,document:card.querySelector(".compare-report-document")}}).filter(item=>item.document).sort((a,b)=>a.rank-b.rank);
 entries.forEach(entry=>{const blocks=reportDiffBlocks(entry.document),denominator=Math.max(1,blocks.length-1);blocks.forEach((item,index)=>{item.node.classList.remove("report-diff-block","report-diff-modified","report-diff-added","report-diff-deleted","report-diff-linked","report-diff-origin","report-diff-jump-target");delete item.node.dataset.reportDiffKind;delete item.node.dataset.reportDiffSlot;item.node.dataset.reportDiffPosition=String(index/denominator)})});
 const baseline=entries[0];entries.slice(1).forEach(entry=>compareReportDocuments(baseline.document,entry.document));
 const counts={modified:0,added:0,deleted:0};root.querySelectorAll("[data-report-diff-kind]").forEach(node=>counts[node.dataset.reportDiffKind]++);
 const summary=root.querySelector("[data-report-diff-summary]");if(summary)summary.innerHTML='<span class="modified">修改 '+counts.modified+'</span><span class="added">新增 '+counts.added+'</span><span class="deleted">删除 '+counts.deleted+'</span><em>点击差异可同步定位其他版本</em>';
}
function clearReportDiffLinks(){$("reportCompareTree").querySelectorAll(".report-diff-linked,.report-diff-origin,.report-diff-jump-target").forEach(node=>node.classList.remove("report-diff-linked","report-diff-origin","report-diff-jump-target"))}
function syncReportDiff(target){
 const root=$("reportCompareTree"),originDocument=target.closest(".compare-report-document"),slot=target.dataset.reportDiffSlot,position=Number(target.dataset.reportDiffPosition||0);clearReportDiffLinks();target.classList.add("report-diff-linked","report-diff-origin");
 root.querySelectorAll(".compare-report-document").forEach(documentNode=>{if(documentNode===originDocument)return;let peer=slot?documentNode.querySelector('[data-report-diff-slot="'+slot+'"]'):null;if(!peer){const blocks=reportDiffBlocks(documentNode);peer=blocks.sort((a,b)=>Math.abs(Number(a.node.dataset.reportDiffPosition||0)-position)-Math.abs(Number(b.node.dataset.reportDiffPosition||0)-position))[0]?.node}if(!peer)return;peer.classList.add("report-diff-linked","report-diff-jump-target");const targetRect=peer.getBoundingClientRect(),viewportRect=documentNode.getBoundingClientRect(),top=documentNode.scrollTop+targetRect.top-viewportRect.top-(documentNode.clientHeight-targetRect.height)/2;documentNode.scrollTo({top:Math.max(0,top),behavior:"smooth"})});
 window.setTimeout(clearReportDiffLinks,1800);
}
function evidenceCompact(value){
 return String(value||"").toLowerCase().replace(/ev-\d+(?:\/\d+)*/g,"").replace(/报告中?|正文|摘要|明确|指出|说明|给出|可见|未见|符合|锚点|关键|判断|素材|支撑|读者|问题/g,"").replace(/[^\p{L}\p{N}%±→+-]+/gu,"");
}
function evidenceGrams(value){
 const text=evidenceCompact(value),grams=new Set();if(text.length<2){if(text)grams.add(text);return grams}for(let i=0;i<text.length-1;i++)grams.add(text.slice(i,i+2));return grams;
}
function evidenceQuoted(value){
 const found=[];String(value||"").replace(/"([^"]{3,})"|'([^']{3,})'|“([^”]{3,})”|‘([^’]{3,})’|「([^」]{3,})」|『([^』]{3,})』|\x60([^\x60]{3,})\x60/g,(match,...parts)=>{const phrase=parts.slice(0,-2).find(Boolean);if(phrase)found.push(evidenceCompact(phrase));return match});return found.filter(Boolean);
}
function reportEvidenceScore(reason,blockText){
 const reasonText=evidenceCompact(reason),block=evidenceCompact(blockText);if(!reasonText||!block)return 0;
 const exact=evidenceQuoted(reason).reduce((total,phrase)=>total+(phrase.length>=3&&block.includes(phrase)?1+Math.min(.8,phrase.length/20):0),0);
 const a=evidenceGrams(reason),b=evidenceGrams(blockText);let overlap=0;a.forEach(token=>{if(b.has(token))overlap++});const dice=a.size+b.size?2*overlap/(a.size+b.size):0;
 const tokenPattern=/[A-Za-z][A-Za-z0-9._+-]{1,}|\d+(?:\.\d+)?%?(?:→\d+(?:\.\d+)?%?)?/g,tokens=(String(reason).match(tokenPattern)||[]).filter(token=>!/^(ev|ai)$/i.test(token)),blockTokens=new Set((String(blockText).match(tokenPattern)||[]).map(token=>token.toLowerCase())),tokenHits=tokens.filter(token=>blockTokens.has(token.toLowerCase())).length;
 return exact+dice+Math.min(.7,tokenHits*.14);
}
function clearRubricEvidence(){
 const root=$("reportCompareTree");root.querySelectorAll(".report-rubric-evidence,.report-rubric-primary").forEach(node=>node.classList.remove("report-rubric-evidence","report-rubric-primary"));root.querySelectorAll(".rubric-evidence-trigger").forEach(button=>{button.classList.remove("active","unmatched");button.setAttribute("aria-pressed","false");delete button.dataset.locateStatus});
}
function locateRubricEvidence(trigger){
 const version=(S.run?.revisions||[]).find(item=>item.version===trigger.dataset.rubricVersion),reason=version?.judgment?.reasoning?.[trigger.dataset.rubricCheck]||"",documentNode=trigger.closest(".report-version-panel")?.querySelector(".compare-report-document");clearRubricEvidence();if(!reason||!documentNode)return;
 const ranked=reportDiffBlocks(documentNode).map(item=>({...item,evidenceScore:reportEvidenceScore(reason,item.key)})).sort((a,b)=>b.evidenceScore-a.evidenceScore),best=ranked[0]?.evidenceScore||0;
 if(best<.13){trigger.classList.add("unmatched");trigger.dataset.locateStatus="未找到可直接定位的报告原文";window.setTimeout(()=>{trigger.classList.remove("unmatched");delete trigger.dataset.locateStatus},2400);return}
 const matches=ranked.filter(item=>item.evidenceScore>=Math.max(.13,best*.82)).slice(0,2);trigger.classList.add("active");trigger.setAttribute("aria-pressed","true");matches.forEach((item,index)=>{item.node.classList.add("report-rubric-evidence");if(!index)item.node.classList.add("report-rubric-primary")});
 const primary=matches[0].node,targetRect=primary.getBoundingClientRect(),viewportRect=documentNode.getBoundingClientRect(),top=documentNode.scrollTop+targetRect.top-viewportRect.top-(documentNode.clientHeight-targetRect.height)/2;documentNode.scrollTo({top:Math.max(0,top),behavior:"smooth"});
}
function reports(){
 const list=S.run?.revisions||[],many=S.versionMode==="many";
 $("versionMode").querySelectorAll("[data-mode]").forEach(button=>button.classList.toggle("on",button.dataset.mode===S.versionMode));
 if(!S.run){document.body.classList.remove("report-version-expanded");$("reportTree").innerHTML='<div class="empty">请先从左侧打开一个 Report Run。</div>';$("reportCompareBoard").hidden=true;$("reportCompareTree").innerHTML="";return}
 if(!list.length){document.body.classList.remove("report-version-expanded");$("reportTree").innerHTML='<div class="empty">当前会话尚未生成报告版本。</div>';$("reportCompareBoard").hidden=true;$("reportCompareTree").innerHTML="";return}
 if(S.version&&!list.some(v=>v.version===S.version))S.version=null;
 const selected=many?null:(list.find(v=>v.version===S.version)||null);
 document.body.classList.toggle("report-version-expanded",!!selected);
 const rows=list.map(v=>{
  const open=v.version===S.version&&!many,j=v.judgment||{},d=j.dimensions||{},models=versionModels(v);
  return '<tr class="report-version-row '+(open?"on":"")+'" data-version="'+esc(v.version)+'"><td><div class="report-version-main"><span class="version-expander">›</span><strong>'+esc(v.version)+'</strong><span class="model-pill">报告生成模型：'+esc(models.runner)+'</span><span class="model-pill">Judge 模型：'+esc(models.judge)+'</span></div></td><td>'+esc(v.parent_version||"—")+'</td><td class="'+(Number(j.overall)>=4?"score-good":"score-warn")+'">'+score(j.overall)+'</td><td class="redline-value'+(redlineCount(j)>0?' has-redline':'')+'">'+redlineCount(j)+'</td>'+EVALUATION_DIMENSIONS.map(([name])=>'<td>'+score(d[name])+'</td>').join("")+'</tr>';
 }).join("");
 $("reportTree").innerHTML='<div class="report-table-wrap"><table class="report-version-table"><thead><tr><th>报告版本</th><th>父版本</th><th>总分</th><th>红线</th>'+EVALUATION_DIMENSIONS.map(([,label])=>'<th>'+label+'</th>').join("")+'</tr></thead><tbody>'+rows+'</tbody></table></div>'+(selected?reportDetail(selected):"");
 $("reportCompareBoard").hidden=!many;
 $("reportCompareTree").innerHTML=many?versionCompare(list):"";
 if(many)requestAnimationFrame(applyReportDiff);
 loadGenerationChains();
 }
function data(){
 const p=S.run?.dataset_preview||S.preview;
 $("structuredOpen").disabled=!p;
 $("fileList").innerHTML=(p?.files||[]).map(f=>'<span class="file-chip">'+esc(f.name)+'</span>').join("")||'<span class="muted">尚未导入数据</span>';
}
function render(){selectors();importState();pipeline();runs();reports();data();updateLoopButton()}
function openData(){
 const p=S.run?.dataset_preview||S.preview;if(!p)return;
 $("structuredTopic").textContent=p.data_id+" / "+p.topic;
 $("datasetFileList").innerHTML=(p.files||[]).map(f=>'<div><b>'+esc(f.name)+'</b><small>'+esc(f.path)+" · "+f.size+' bytes</small></div>').join("");
 $("structuredSource").textContent=p.structured_source||"";
 $("structuredView").innerHTML="<pre>"+esc(JSON.stringify(p.structured_data,null,2))+"</pre>";
 $("structuredModal").hidden=false;$("structuredModal").setAttribute("aria-hidden","false");
}
async function importData(){
 if(isImported()){openData();return}
 const data_id=$("datasetSelect").value,case_id=$("caseSelect").value,skill_template_id=$("skillTemplate").value;
 if(!data_id||!case_id||!skill_template_id)return error(new Error("请选择数据集、Case 和 Skill 模板"));
 S.busy=true;pipeline();
 try{
  const stop_policy={overall_target:Number($("loopStopScore").value),max_no_improvement:Number($("maxNoImprovement").value),max_elapsed_seconds:Number($("maxElapsedMinutes").value)*60,stop_on_unrepairable_failure:$("stopOnUnrepairable").checked};
  const {run}=await api("/api/report-loop/runs",{method:"POST",body:JSON.stringify({data_id,case_id,skill_template_id,requirement:$("reportRequirement").value,stop_policy})});
  S.run=run;S.preview=run.dataset_preview;S.key=key();S.version=null;await refreshRuns();
 }catch(e){error(e)}finally{S.busy=false;render()}
}
async function poll(id){
 for(;;){const {job}=await api("/api/report-loop/job?id="+encodeURIComponent(id));if(job.status==="completed")return;if(job.status==="failed")throw new Error(job.error||"任务失败");await new Promise(r=>setTimeout(r,1200))}
}
async function act(role,{reportError=true}={}){
 if(!S.run)return;S.busy=true;S.runningRole=role;render();
 try{const name=role==="runner"?"generate":"judge",body={id:S.run.id};const backend=S[role+"Backend"]||"workbuddy";if(role==="runner"){body.generation_backend=backend;if(backend==="workbuddy")body.model=S.runnerModel;else if(backend==="codex"){body.model=S.runnerCodexModel;body.reasoning_effort=S.runnerCodexReasoning}else body.model=S.runnerApiModel}else{body.llm_backend=backend;if(backend==="workbuddy")body.llm_model=S.judgeModel;else if(backend==="codex"){body.llm_model=S.judgeCodexModel;body.llm_reasoning_effort=S.judgeCodexReasoning}else body.llm_model=S.judgeApiModel}const {job}=await api("/api/report-loop/"+name,{method:"POST",body:JSON.stringify(body)});await poll(job.id)}
 catch(e){if(reportError)error(e);throw e}finally{S.busy=false;S.runningRole=null;await refreshRun();await refreshRuns();render()}
}
async function runLoop(){
 if(!S.run||S.busy||S.loopRunning)return;
 if(!step("runner").on&&!step("judge").on)return;
 S.loopRunning=true;updateLoopButton();
 const sig=()=>{const v=last(S.run);return (S.run.revisions?.length||0)+"|"+(v?.status==="judged");};
 try{
  let prev=sig(),guard=0;
  while(guard++<50){
   if(S.run.stop_state?.stopped)break;
   const runnerOn=step("runner").on,judgeOn=step("judge").on;
   if(!runnerOn&&!judgeOn)break;
   if(runnerOn)await act("runner",{reportError:false});else await act("judge",{reportError:false});
   const cur=sig();
   if(S.run.stop_state?.stopped)break;
   if(cur===prev)break;
   prev=cur;
  }
 }catch(e){error(e)}finally{S.loopRunning=false;render();}
}
function updateLoopButton(){
 const b=$("loopStart");if(!b)return;
 const stopped=!!S.run?.stop_state?.stopped;
 const eligible=!!S.run&&!S.busy&&!S.loopRunning&&!stopped&&(step("runner").on||step("judge").on);
 b.disabled=!eligible;
 b.textContent=S.loopRunning?"Loop 运行中…":(stopped?"已停止（达标/达上限）":"一键启动 Loop");
 b.setAttribute("aria-busy",S.loopRunning?"true":"false");
}
async function refreshRun(){if(S.run){const {run}=await api("/api/report-loop/run?id="+encodeURIComponent(S.run.id));S.run=run;S.preview=run.dataset_preview}}
async function refreshRuns(){const x=await api("/api/report-loop/runs");S.runs=x.runs||[];if(S.run)S.run=S.runs.find(r=>r.id===S.run.id)||S.run}
async function openRun(id){
 const {run}=await api("/api/report-loop/run?id="+encodeURIComponent(id));S.run=run;S.preview=run.dataset_preview;S.key=run.data_id+"|"+run.case_id;
 $("datasetSelect").value=run.data_id;fillCases(run.case_id);$("skillTemplate").value=run.skill_template_id;$("reportRequirement").value=run.requirement||"";$("loopStopScore").value=run.stop_policy.overall_target;$("maxNoImprovement").value=run.stop_policy.max_no_improvement;$("maxElapsedMinutes").value=Math.ceil((run.stop_policy.max_elapsed_seconds||3600)/60);$("stopOnUnrepairable").checked=run.stop_policy.stop_on_unrepairable_failure===true;S.version=null;S.traceVersion=null;S.versionMode="one";S.compareVersions=[];S.compareTraces.clear();render();
}
function openSkill(){
 const x=template();if(!x)return;$("skillModalTitle").textContent=(x.label||x.id)+" Skill";$("skillFileName").textContent=x.skill_path||"SKILL.md";$("instructionFileName").textContent=x.instruction_path||"instructions.md";$("skillDoc").innerHTML="<pre>"+esc(x.skill)+"</pre>";$("instructionDoc").innerHTML="<pre>"+esc(x.instruction)+"</pre>";$("skillModal").hidden=false;$("skillModal").setAttribute("aria-hidden","false");
}
function close(id){$(id).hidden=true;$(id).setAttribute("aria-hidden","true")}
function reset(){S.run=null;S.preview=null;S.key="";S.version=null;S.versionMode="one";S.compareVersions=[];S.compareTraces.clear();$("datasetLoadStatus").classList.remove("is-error");render()}
const rawReports=reports,rawOpenData=openData,rawOpenSkill=openSkill;
reports=()=>{rawReports();formatReportDocuments()};
openData=()=>{rawOpenData();formatDatasetPreview()};
openSkill=()=>{rawOpenSkill();formatSkillDocuments()};
$("datasetSelect").onchange=()=>{fillCases();reset()};$("caseSelect").onchange=reset;$("skillTemplate").onchange=reset;
$("datasetLoad").onclick=importData;$("structuredOpen").onclick=openData;$("structuredClose").onclick=()=>close("structuredModal");$("skillOpen").onclick=openSkill;$("skillClose").onclick=()=>close("skillModal");$("loopStart").onclick=runLoop;
$("pipeline").onclick=e=>{const b=e.target.closest("[data-role]");if(b)act(b.dataset.role).catch(()=>{})};
$("pipeline").onchange=e=>{const role=e.target.dataset.modelRole;if(role)S[role+"Model"]=e.target.value;const backendRole=e.target.dataset.backendRole;if(backendRole!==undefined){S[backendRole+"Backend"]=e.target.value;render();}const reasoningRole=e.target.dataset.reasoningRole;if(reasoningRole!==undefined){S[reasoningRole+"CodexReasoning"]=e.target.value;}};
$("latestBody").onclick=e=>{const row=e.target.closest("[data-run]");if(row)openRun(row.dataset.run).catch(error)};
$("versionMode").onclick=e=>{const button=e.target.closest("[data-mode]");if(!button)return;S.versionMode=button.dataset.mode==="many"?"many":"one";if(S.versionMode==="many")S.version=null;reports()};
$("reportCompareTree").onchange=e=>{const select=e.target.closest("[data-compare-index]");if(!select)return;const index=Number(select.dataset.compareIndex);if(S.compareVersions.some((value,i)=>i!==index&&value===select.value)){reports();return}S.compareVersions[index]=select.value;S.compareTraces.clear();reports()};
$("reportCompareTree").onclick=e=>{const rubric=e.target.closest("[data-rubric-version][data-rubric-check]");if(rubric){locateRubricEvidence(rubric);return}const diff=e.target.closest("[data-report-diff-slot]");if(diff){syncReportDiff(diff);return}const trace=e.target.closest("[data-compare-trace]");if(trace){const version=trace.dataset.compareTrace;S.compareTraces.has(version)?S.compareTraces.delete(version):S.compareTraces.add(version);reports();return}const remove=e.target.closest("[data-remove-compare]");if(remove&&S.compareVersions.length>2){S.compareVersions.splice(Number(remove.dataset.removeCompare),1);S.compareTraces.clear();reports();return}if(e.target.closest("[data-add-compare]")){const used=new Set(S.compareVersions),candidate=(S.run?.revisions||[]).find(v=>!used.has(v.version));if(candidate&&S.compareVersions.length<3){S.compareVersions.push(candidate.version);S.compareTraces.clear();reports()}}};$("reportTree").onclick=e=>{const trace=e.target.closest("[data-trace-version]");if(trace){S.traceVersion=S.traceVersion===trace.dataset.traceVersion?null:trace.dataset.traceVersion;reports();return}const node=e.target.closest("[data-version]");if(node){S.version=S.version===node.dataset.version?null:node.dataset.version;S.traceVersion=null;reports()}};
$("openSession").onclick=()=>$("sessionSelect").value&&openRun($("sessionSelect").value).catch(error);
$("railToggle").onclick=()=>document.body.classList.add("rail-collapsed");$("railReopen").onclick=()=>document.body.classList.remove("rail-collapsed");
Promise.allSettled([api("/api/data/options"),api("/api/skill/templates"),api("/api/report-loop/runs"),api("/api/generation/config")]).then(async results=>{const [d,t,r,c]=results.map(item=>item.status==="fulfilled"?item.value:null);S.datasets=d?.datasets||[];S.templates=t?.templates||[];S.runs=r?.runs||[];S.config=c||{};S.runnerModel=S.config.model||S.config.models?.[0]||"";S.judgeModel=S.config.judge_wb_model||S.config.evaluation_models?.[0]||"";S.runnerBackend="workbuddy";S.runnerApiModel=S.config.api_model_default||S.config.api_models?.[0]||"";S.runnerCodexModel=S.config.codex_model_default||S.config.codex_models?.[0]||"";S.runnerCodexReasoning=S.config.codex_reasoning_effort_default||S.config.codex_reasoning_efforts?.[0]||"";S.judgeBackend="workbuddy";S.judgeApiModel=S.config.api_model_default||S.config.api_models?.[0]||"";S.judgeCodexModel=S.config.codex_model_default||S.config.codex_models?.[0]||"";S.judgeCodexReasoning=S.config.codex_reasoning_effort_default||S.config.codex_reasoning_efforts?.[0]||"";const id=new URLSearchParams(location.search).get("id")||S.runs[0]?.id;if(id)await openRun(id);else render();const failed=results.filter(item=>item.status==="rejected");if(failed.length)error(new Error(failed.map(item=>item.reason?.message||String(item.reason)).join("；")))}).catch(error);




