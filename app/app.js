// app.js — OpenHarness 单页前端逻辑 (owner: M4)
// 由 index.html 抽出;改后必须 node --check app.js。DIMS/ZH 随后端 STATE.dims/dim_zh 刷新。

// 维度随产品变化(算数字型4维 / 调研洞察6维), 由后端 STATE.dims / STATE.dim_zh 提供;
// 会话未加载前用算数字型作默认。render() 里按 STATE 刷新。
let DIMS=["data_accuracy","completeness","insight","conciseness"];
let ZH={data_accuracy:"数据准确性",completeness:"完整性",insight:"洞察质量",conciseness:"简洁性"};
let SID=null, STATE=null;
let GEN_JOB=null, GEN_CONFIG=null, GEN_POLL=null;
let JUDGE_SUMMARY=null, JUDGE_RESULTS=[], JUDGE_RUNNING=false;
const GEN_TERMINAL_SEEN=new Set();

function toast(m,ms=2600){const t=document.getElementById('toast');t.textContent=m;t.style.display='block';
  clearTimeout(t._t);t._t=setTimeout(()=>t.style.display='none',ms);}
async function api(path,method,body){
  const opt={method,headers:{'Content-Type':'application/json'}};
  if(body)opt.body=JSON.stringify(body);
  const r=await fetch(path,opt);
  if(r.status===401){authWall();throw new Error('未登录');}
  const j=await r.json();
  if(!r.ok){
    toast(j.error||('错误 '+r.status));
    const err=new Error(j.error||String(r.status));
    err.status=r.status;
    throw err;
  }
  return j;
}
// iOA 未登录/身份校验失败: 整页拦截提示(而非吞成普通 toast)
function authWall(){
  if(document.getElementById('authWall'))return;
  const d=document.createElement('div');
  d.id='authWall';
  d.style.cssText='position:fixed;inset:0;z-index:99;background:rgba(15,18,22,.96);'
    +'display:flex;align-items:center;justify-content:center;text-align:center;padding:24px';
  d.innerHTML='<div style="max-width:460px"><h2 style="color:var(--acc);margin:0 0 12px">需经 iOA 登录访问</h2>'
    +'<p class="mut">未检测到有效的 iOA 身份，或身份校验失败。<br>请通过公司 iOA 网关（内网域名）访问本平台，不要直连端口。</p></div>';
  document.body.appendChild(d);
}
function fmt(x,d=2){return x==null?'-':Number(x).toFixed(d);}
function bar(v,max=5){return `<div class="barwrap"><div class="bar" style="width:${(v/max*100)||0}%"></div></div>`;}
function esc(x){return String(x==null?'':x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function populateEvaluationModelSelect(id,defaultModel){
  const el=document.getElementById(id);
  if(!el||!GEN_CONFIG)return;
  const models=Array.isArray(GEN_CONFIG.evaluation_models)
    ?GEN_CONFIG.evaluation_models:[];
  const desired=el.value||defaultModel||GEN_CONFIG.evaluation_model_default||'claude-opus-4.8';
  const signature=models.join('\n');
  if(el.dataset.models!==signature){
    el.innerHTML=models.map(model=>`<option value="${esc(model)}">${esc(model)}</option>`).join('');
    el.dataset.models=signature;
  }
  el.value=models.includes(desired)
    ?desired:(GEN_CONFIG.evaluation_model_default||models[0]||'');
}
function populateApiModelInput(id,defaultModel){
  const el=document.getElementById(id);
  if(!el||!GEN_CONFIG)return;
  const models=Array.isArray(GEN_CONFIG.api_models)?GEN_CONFIG.api_models:[];
  const list=document.getElementById('evaluationApiModels');
  const signature=models.join('\n');
  if(list&&list.dataset.models!==signature){
    list.innerHTML=models.map(model=>`<option value="${esc(model)}"></option>`).join('');
    list.dataset.models=signature;
  }
  if(!el.dataset.initialized){
    el.value=defaultModel||GEN_CONFIG.api_model_default||'claude-opus-4.8';
    el.dataset.initialized='1';
  }
}
function syncLlmBackendControls(kind){
  const backend=document.getElementById(kind+'LlmBackend');
  const isWorkbuddy=backend&&backend.value==='workbuddy';
  const wbWrap=document.getElementById(kind+'WbModelWrap');
  const apiWrap=document.getElementById(kind+'ApiModelWrap');
  if(wbWrap)wbWrap.style.display=isWorkbuddy?'block':'none';
  if(apiWrap)apiWrap.style.display=isWorkbuddy?'none':'block';
}
function readLlmSelection(kind){
  const backend=(document.getElementById(kind+'LlmBackend')||{}).value||'workbuddy';
  const result={llm_backend:backend};
  const inputId=kind+(backend==='workbuddy'?'WbModel':'ApiModel');
  const model=((document.getElementById(inputId)||{}).value||'').trim();
  if(!model){
    toast((kind==='judge'?'Judge':'Optimizer')+' '+(backend==='workbuddy'?'WB':'API')+' 模型不能为空');
    return null;
  }
  result.llm_model=model;
  return result;
}
document.getElementById('judgeLlmBackend').onchange=()=>syncLlmBackendControls('judge');
document.getElementById('optimizerLlmBackend').onchange=()=>syncLlmBackendControls('optimizer');

// ---- 1. 生成 V0 ----
function syncV0StrategyVisibility(){
  const llmMode=(document.getElementById('optModeSel')||{}).value==='llm_rewrite';
  const wrap=document.getElementById('v0StrategyWrap');
  if(wrap)wrap.style.display=llmMode?'block':'none';
}
document.getElementById('optModeSel').onchange=syncV0StrategyVisibility;
syncV0StrategyVisibility();
document.getElementById('genBtn').onclick=async()=>{
  const btn=document.getElementById('genBtn');
  const req=document.getElementById('reqInput').value.trim();
  if(!req){toast('请先填写需求描述');return;}
  const pid=document.getElementById('pidInput').value.trim();
  const optMode=(document.getElementById('optModeSel')||{}).value||'switch_search';
  const v0Strategy=optMode==='llm_rewrite'
    ?((document.getElementById('v0StrategySel')||{}).value||'base_skill')
    :'base_skill';
  const stopOverall=Number(document.getElementById('stopOverall').value);
  const stopPatience=Number(document.getElementById('stopPatience').value);
  if(optMode==='llm_rewrite'&&(!(stopOverall>=1&&stopOverall<=5)||!Number.isInteger(stopPatience)||stopPatience<1)){
    toast('LLM loop 停止 overall 须为 1–5，连续无提升版本数须为正整数');return;
  }
  const optimizerStop=optMode==='llm_rewrite'
    ?{overall_target:stopOverall,max_no_improvement:stopPatience}
    :{};
  const oldText=btn.textContent;
  btn.disabled=true;
  btn.textContent=v0Strategy==='llm_scratch'?'⏳ LLM 正在起草 V0…':'⏳ 正在生成 V0…';
  try{
    const j=await api('/api/session','POST',{
      requirement:req,
      product_id:pid,
      optimizer_mode:optMode,
      v0_strategy:v0Strategy,
      optimizer_stop:optimizerStop
    });
    SID=j.session_id; STATE=j; GEN_JOB=null; JUDGE_SUMMARY=null; JUDGE_RESULTS=[]; render();
    await loadSessions();
    const sessSel=document.getElementById('sessSel'); if(sessSel)sessSel.value=SID;
    toast('已生成 V0：'+j.product_id);
  }finally{
    btn.disabled=false;
    btn.textContent=oldText;
  }
};

// ---- 2. 导入数据 ----
document.getElementById('sampleBtn').onclick=async()=>{
  if(!SID){toast('请先生成 V0');return;}
  const j=await api('/api/data','POST',{id:SID,use_sample:true});
  STATE=j; render(); toast('已导入内置样例：'+j.n_cases+' 条');
};
document.getElementById('configuredDataBtn').onclick=async()=>{
  if(!SID){toast('请先生成 V0');return;}
  const j=await api('/api/data','POST',{id:SID,use_configured:true});
  STATE=j; render(); toast('已加载当前 WB 数据集：'+j.n_cases+' 条');
};
document.getElementById('importBtn').onclick=async()=>{
  if(!SID){toast('请先生成 V0');return;}
  const raw=document.getElementById('dataInput').value.trim();
  if(!raw){toast('请粘贴数据或用样例');return;}
  let rows;
  try{
    rows=(raw[0]==='['||raw[0]==='{')
      ?JSON.parse(raw)
      :raw.split('\n').filter(x=>x.trim()).map(x=>JSON.parse(x));
  }
  catch(e){toast('解析失败：'+e.message);return;}
  const j=await api('/api/data','POST',{id:SID,rows}); STATE=j; render(); toast('已导入 '+j.n_cases+' 条');
};

// ---- 3. 导入/补充报告文本 ----
document.getElementById('importOutBtn').onclick=async()=>{
  if(!SID){toast('请先生成 V0 并导入数据');return;}
  const cid=document.getElementById('outCaseSel').value;
  const txt=document.getElementById('outText').value.trim();
  if(!cid){toast('请选择 case');return;}
  if(!txt){toast('请粘贴报告正文');return;}
  const j=await api('/api/import_output','POST',{id:SID,version:STATE.current_version,case_id:cid,report_text:txt});
  STATE=j; document.getElementById('outText').value=''; render();
  toast('已导入 '+cid+' 的报告（'+txt.length+' 字）');
};

// ---- 3a. 上传报告文件 ----
document.getElementById('uploadBtn').onclick=async()=>{
  if(!SID){toast('请先生成 V0 并导入数据');return;}
  const cid=document.getElementById('outCaseSel').value;
  if(!cid){toast('请选择 case');return;}
  const f=document.getElementById('outFile').files[0];
  if(!f){toast('请选文件');return;}
  const name=f.name.toLowerCase();
  try{
    if(name.endsWith('.md')||name.endsWith('.txt')){
      const txt=await f.text();
      const j=await api('/api/import_output','POST',{id:SID,version:STATE.current_version,case_id:cid,report_text:txt});
      STATE=j; render(); toast('已上传 '+f.name);
    }else{
      const b64=await new Promise((res,rej)=>{const fr=new FileReader();fr.onload=()=>res(fr.result.split(',')[1]);fr.onerror=rej;fr.readAsDataURL(f);});
      const j=await api('/api/upload_report','POST',{id:SID,version:STATE.current_version,case_id:cid,filename:f.name,content_b64:b64});
      STATE=j; render(); toast('已上传并解析 '+f.name);
    }
  }catch(e){/* api() 已 toast 错误 */}
};

// ---- 3b. 一键批量跑模型 Judge ----
document.getElementById('runJudgeBtn').onclick=async()=>{
  if(!SID){toast('请先生成 V0 并导入数据');return;}
  const progress=STATE&&STATE.judge_progress;
  if(!progress||progress.reports_ready!==progress.total_cases){
    toast('请先生成或补齐当前版本全部 case 的报告');return;
  }
  const parallel=readParallel(
    'judgeParallel',
    GEN_CONFIG&&GEN_CONFIG.judge_parallel,
    'Judge'
  );
  if(parallel==null)return;
  const llm=readLlmSelection('judge');
  if(!llm)return;
  JUDGE_RUNNING=true;renderJudgeStatus();
  toast(`正在以并发 ${parallel} Judge ${progress.reports_ready}/${progress.total_cases} 份报告…`,9000);
  try{
    const j=await api('/api/run_judge_batch','POST',{
      id:SID,version:STATE.current_version,parallel,...llm
    });
    STATE=j.state;JUDGE_SUMMARY=j.summary;JUDGE_RESULTS=j.results||[];render();
    const s=j.summary;
    toast(
      s.status==='completed'
        ?`批量 Judge 完成：${s.judged_cases}/${s.total_cases}`
        :`批量 Judge ${s.status}：成功 ${s.judged_cases}/${s.total_cases}`,
      5200
    );
  }catch(e){
    /* api() 已 toast 错误(如无 key) */
  }finally{
    JUDGE_RUNNING=false;render();
  }
};
document.getElementById('rubricSaveBtn').onclick=async()=>{
  if(!STATE){return;}
  const weights={},target={};
  document.querySelectorAll('#rubricEditor input.w').forEach(i=>weights[i.dataset.dim]=parseFloat(i.value)||0);
  const ov=document.querySelector('#rubricEditor input.tgt-overall'); if(ov)target.overall=parseFloat(ov.value)||0;
  const j=await api('/api/rubric','POST',{id:SID,weights,target}); STATE=j; render(); toast('rubric 已更新为 '+j.rubric.version);
};

// ---- 推进下一版 ----
document.getElementById('advanceBtn').onclick=async()=>{
  const btn=document.getElementById('advanceBtn');
  const msg=document.getElementById('advanceMsg');
  const oldText=btn.textContent;
  const llmMode=STATE&&STATE.optimizer_mode==='llm_rewrite';
  // 进入 loading 态: 禁用按钮 + 明确提示(LLM 改写可能耗时数十秒, 避免"点了没反应"的错觉)
  btn.disabled=true;
  btn.textContent=llmMode?'⏳ 改写中…':'⏳ 生成中…';
  if(msg)msg.innerHTML='<span class="mut">⏳ '+(llmMode?'正在调用 LLM 改写下一版，通常需数十秒，请稍候…':'正在生成下一版，请稍候…')+'</span>';
  try{
    const llm=readLlmSelection('optimizer');
    if(!llm){btn.disabled=false;btn.textContent=oldText;return;}
    const j=await api('/api/advance','POST',{id:SID,...llm}); STATE=j; render();
    const r=j.advance_result;
    if(r){ toast(r.message, 5000);
      document.getElementById('advanceMsg').innerHTML =
        (r.status==='adopted'?'<span class="ok-txt">✅ 采纳</span> ':
         r.status==='proposed'?'<span class="warn-txt">🧪 待真实验证</span> ':
         r.status==='rejected'?'<span class="warn-txt">❌ 被 gate 拒绝</span> ':
         r.status==='blocked'?'<span class="warn-txt">⚠️ 无法推进</span> ':
         '<span class="mut">■ 收敛</span> ')+r.message;
    }else{
      // 理论上 llm_rewrite/switch_search 都会带 advance_result; 兜底避免"无任何显示"
      if(msg)msg.innerHTML='<span class="mut">已处理，但未返回结果说明。</span>';
    }
  }catch(e){
    // 请求本身失败(如 LLM 未配置 key / 超时 / 500): 明确报错, 不再静默
    toast('推进失败：'+(e.message||e), 6000);
    const m2=document.getElementById('advanceMsg');
    if(m2)m2.innerHTML='<span class="warn-txt">❌ 请求失败</span> '+(e.message||String(e));
    // render() 未跑到, 手动恢复按钮可用态
    btn.disabled=false; btn.textContent=oldText;
  }
};

// ---- 真实运行 · WB CLI ----
const GEN_JOB_STATUS_ZH={
  queued:'等待任务执行槽',running:'生成中',retrying:'自动重试中',importing:'导入中',
  cancel_requested:'等待安全停止',completed:'已完成',partial:'部分成功',
  failed:'失败',cancelled:'已取消',interrupted:'服务重启中断'
};
const GEN_CASE_STATUS_ZH={
  queued:'待生成',running:'生成中',retrying:'自动重试中',
  generated:'已生成',imported:'已导入',retry_exhausted:'重试耗尽',
  failed:'失败',cancelled:'已取消',interrupted:'服务重启中断'
};
function generationActive(){
  return !!(GEN_JOB&&GEN_JOB.active);
}
function readParallel(id,fallback,label){
  const input=document.getElementById(id);
  const value=Number(input&&input.value||fallback);
  if(!Number.isInteger(value)||value<1){
    toast(`${label}并发必须是大于等于 1 的整数`);
    return null;
  }
  return value;
}
function scheduleGenerationPoll(){
  clearTimeout(GEN_POLL);
  if(!GEN_JOB||!GEN_JOB.active)return;
  GEN_POLL=setTimeout(pollGeneration,1200);
}
async function pollGeneration(){
  if(!GEN_JOB)return;
  try{
    GEN_JOB=await api('/api/generation?id='+encodeURIComponent(GEN_JOB.job_id),'GET');
    renderGenerationPanel();
    if(GEN_JOB.active){scheduleGenerationPoll();return;}
    if(!GEN_TERMINAL_SEEN.has(GEN_JOB.job_id)){
      GEN_TERMINAL_SEEN.add(GEN_JOB.job_id);
      const j=await api('/api/session?id='+encodeURIComponent(SID),'GET');
      STATE=j; render();
      const msg=GEN_JOB.status==='completed'
        ?`真实报告已生成并导入：${GEN_JOB.imported_count}/${GEN_JOB.case_count}`
        :`真实运行结束：${GEN_JOB_STATUS_ZH[GEN_JOB.status]||GEN_JOB.status}，已导入 ${GEN_JOB.imported_count}/${GEN_JOB.case_count}`;
      toast(msg,5200);
    }
  }catch(e){clearTimeout(GEN_POLL);}
}
async function loadLatestGeneration(){
  clearTimeout(GEN_POLL);GEN_JOB=null;
  if(!SID){renderGenerationPanel();return;}
  try{
    const j=await api('/api/generation?session_id='+encodeURIComponent(SID),'GET');
    GEN_JOB=j.job||null;
    renderGenerationPanel();
    scheduleGenerationPoll();
  }catch(e){renderGenerationPanel();}
}
document.getElementById('runGenerationBtn').onclick=async()=>{
  if(!SID||!STATE||!STATE.n_cases){toast('请先导入评测数据');return;}
  const parallel=readParallel(
    'generationParallel',
    GEN_CONFIG&&GEN_CONFIG.parallel,
    '报告生成'
  );
  if(parallel==null)return;
  const modelInput=document.getElementById('generationModel');
  const model=(modelInput&&modelInput.value||'').trim();
  if(!model){toast('报告生成模型不能为空');return;}
  const key='start-'+SID+'-'+STATE.current_version+'-'+Date.now();
  try{
    const j=await api('/api/generation/start','POST',{
      id:SID,idempotency_key:key,parallel,model
    });
    GEN_JOB=j.job;renderGenerationPanel();scheduleGenerationPoll();
    toast(j.reused?'已有任务正在执行':`WB CLI 任务已启动：${model}，并发 ${parallel}`);
  }catch(e){renderGenerationPanel();}
};
document.getElementById('retryGenerationBtn').onclick=async()=>{
  if(!GEN_JOB)return;
  const parallel=readParallel(
    'generationParallel',
    GEN_JOB.parallel,
    '报告生成'
  );
  if(parallel==null)return;
  const modelInput=document.getElementById('generationModel');
  const model=(modelInput&&modelInput.value||GEN_JOB.model||'').trim();
  if(!model){toast('报告生成模型不能为空');return;}
  try{
    const j=await api('/api/generation/retry','POST',{
      job_id:GEN_JOB.job_id,
      idempotency_key:'retry-'+GEN_JOB.job_id+'-'+Date.now(),
      parallel,model
    });
    GEN_JOB=j.job;renderGenerationPanel();scheduleGenerationPoll();
    toast(`失败 case 重试已启动：${model}，并发 ${parallel}`);
  }catch(e){renderGenerationPanel();}
};
document.getElementById('cancelGenerationBtn').onclick=async()=>{
  if(!GEN_JOB)return;
  try{
    const j=await api('/api/generation/cancel','POST',{job_id:GEN_JOB.job_id});
    GEN_JOB=j.job;renderGenerationPanel();scheduleGenerationPoll();
    toast('已请求安全停止；当前 CLI 轮次结束后生效');
  }catch(e){renderGenerationPanel();}
};
function renderGenerationPanel(){
  const cfg=document.getElementById('generationConfig');
  const status=document.getElementById('generationStatus');
  const cases=document.getElementById('generationCases');
  const run=document.getElementById('runGenerationBtn');
  const retry=document.getElementById('retryGenerationBtn');
  const cancel=document.getElementById('cancelGenerationBtn');
  const parallelInput=document.getElementById('generationParallel');
  const modelInput=document.getElementById('generationModel');
  const judgeParallelInput=document.getElementById('judgeParallel');
  if(!cfg)return;

  if(GEN_CONFIG){
    const judgeBackend=document.getElementById('judgeLlmBackend');
    const optimizerBackend=document.getElementById('optimizerLlmBackend');
    if(judgeBackend&&!judgeBackend.dataset.initialized){
      judgeBackend.value=GEN_CONFIG.judge_llm_backend||'workbuddy';
      judgeBackend.dataset.initialized='1';
    }
    if(optimizerBackend&&!optimizerBackend.dataset.initialized){
      optimizerBackend.value=GEN_CONFIG.optimizer_llm_backend||'workbuddy';
      optimizerBackend.dataset.initialized='1';
    }
    populateEvaluationModelSelect('judgeWbModel',GEN_CONFIG.judge_wb_model);
    populateEvaluationModelSelect('optimizerWbModel',GEN_CONFIG.optimizer_wb_model);
    populateApiModelInput('judgeApiModel',GEN_CONFIG.judge_api_model);
    populateApiModelInput('optimizerApiModel',GEN_CONFIG.optimizer_api_model);
    syncLlmBackendControls('judge');
    syncLlmBackendControls('optimizer');
  }

  if(modelInput&&GEN_CONFIG&&Array.isArray(GEN_CONFIG.models)){
    const desired=modelInput.value||GEN_CONFIG.model||'deepseek-v4-pro-ioa';
    const models=GEN_CONFIG.models.slice();
    if(desired&&!models.includes(desired))models.push(desired);
    const signature=models.join('\n');
    if(modelInput.dataset.models!==signature){
      modelInput.innerHTML=models.map(model=>
        `<option value="${esc(model)}">${esc(model)}</option>`
      ).join('');
      modelInput.dataset.models=signature;
    }
    modelInput.value=models.includes(desired)
      ?desired:(GEN_CONFIG.model||models[0]||'');
  }

  if(!GEN_CONFIG){
    cfg.textContent='正在读取运行配置…';
  }else if(!GEN_CONFIG.ready){
    cfg.innerHTML='<span class="warn-txt">运行配置不可用：'+esc(GEN_CONFIG.error)+'</span>';
  }else{
    cfg.innerHTML=`<div class="kv"><span>执行 Skill</span><span>启动时编译当前 Session 版本</span></div>`+
      `<div class="kv"><span>模型 / 默认并发</span><span>${esc(GEN_CONFIG.model||'CLI默认')} / ${GEN_CONFIG.parallel}</span></div>`+
      `<div class="kv"><span>报告重试</span><span>最多额外 ${GEN_CONFIG.max_report_retries} 次</span></div>`+
      `<div class="kv"><span>Judge 重试</span><span>最多额外 ${GEN_CONFIG.judge_max_retries} 次</span></div>`+
      `<div class="small mut" style="margin-top:5px">每个任务冻结完整 Skill 目录、版本和哈希，WB CLI 只执行该副本。</div>`;
  }
  const active=generationActive();
  if(parallelInput&&GEN_CONFIG){
    if(!parallelInput.dataset.initialized){
      parallelInput.value=GEN_CONFIG.parallel;
      parallelInput.dataset.initialized='1';
    }
    parallelInput.disabled=active||!GEN_CONFIG.ready;
  }
  if(modelInput&&GEN_CONFIG){
    if(!modelInput.dataset.initialized){
      modelInput.value=GEN_CONFIG.model||'deepseek-v4-pro-ioa';
      modelInput.dataset.initialized='1';
    }
    modelInput.disabled=active||!GEN_CONFIG.ready;
  }
  if(
    GEN_JOB&&!active&&modelInput&&parallelInput
    &&modelInput.dataset.jobId!==GEN_JOB.job_id
  ){
    modelInput.value=GEN_JOB.model||(GEN_CONFIG&&GEN_CONFIG.model)||'deepseek-v4-pro-ioa';
    parallelInput.value=GEN_JOB.parallel||(GEN_CONFIG&&GEN_CONFIG.parallel)||20;
    modelInput.dataset.jobId=GEN_JOB.job_id;
  }
  if(judgeParallelInput&&GEN_CONFIG){
    if(!judgeParallelInput.dataset.initialized){
      judgeParallelInput.value=GEN_CONFIG.judge_parallel;
      judgeParallelInput.dataset.initialized='1';
    }
    judgeParallelInput.disabled=active||JUDGE_RUNNING;
  }
  ['judge','optimizer'].forEach(kind=>{
    const backend=document.getElementById(kind+'LlmBackend');
    const model=document.getElementById(kind+'WbModel');
    if(backend)backend.disabled=active||JUDGE_RUNNING;
    if(model)model.disabled=active||JUDGE_RUNNING;
  });
  const generationAction=STATE&&STATE.actions&&STATE.actions.run_generation;
  run.disabled=!STATE||!(generationAction?generationAction.enabled:STATE.n_cases)||active||!GEN_CONFIG||!GEN_CONFIG.ready;
  retry.style.display=GEN_JOB&&!active&&GEN_JOB.failed_case_ids&&GEN_JOB.failed_case_ids.length?'':'none';
  cancel.style.display=active?'':'none';
  const advanceAction=STATE&&STATE.actions&&STATE.actions.advance;
  document.getElementById('advanceBtn').disabled=!STATE||!(advanceAction?advanceAction.enabled:STATE.can_advance)||active||JUDGE_RUNNING;
  if(!GEN_JOB){
    status.innerHTML='<span class="mut">尚未运行。报告导入后，请在下方一键批量 Judge 全部 case。</span>';
    cases.innerHTML='';return;
  }
  const total=GEN_JOB.case_count||0, imported=GEN_JOB.imported_count||0;
  const done=(GEN_JOB.cases||[]).filter(x=>x.imported||['retry_exhausted','failed','cancelled'].includes(x.status)).length;
  const pct=total?Math.round(done/total*100):0;
  const historical=STATE&&GEN_JOB.skill_version!==STATE.current_version;
  status.innerHTML=`<div class="kv"><span>状态</span><b class="${GEN_JOB.status==='completed'?'ok-txt':GEN_JOB.status==='failed'?'warn-txt':''}">${esc(GEN_JOB_STATUS_ZH[GEN_JOB.status]||GEN_JOB.status)}</b></div>`+
    `<div class="kv"><span>实际执行版本</span><span>${esc(GEN_JOB.skill_version)} · ${esc((GEN_JOB.execution_skill_hash||'').slice(0,10))}</span></div>`+
    `<div class="kv"><span>报告生成模型</span><span>${esc(GEN_JOB.model||'CLI 默认')}</span></div>`+
    `<div class="kv"><span>报告生成并发</span><span>${GEN_JOB.parallel}</span></div>`+
    (historical?`<div class="warn-txt" style="margin-top:5px">这是历史任务；当前 Session 已是 ${esc(STATE.current_version)}。</div>`:'')+
    `<div class="kv"><span>已导入</span><span>${imported}/${total}</span></div>`+
    `<div class="barwrap" style="margin-top:7px"><div class="bar" style="width:${pct}%"></div></div>`+
    (GEN_JOB.error?`<div class="warn-txt" style="margin-top:6px">${esc(GEN_JOB.error)}</div>`:'');
  cases.innerHTML=(GEN_JOB.cases||[]).map(c=>
    `<div class="job-case"><div class="job-case-main">`+
    `<span class="status-dot ${esc(c.status)}"></span>`+
    `<b class="job-case-id" title="${esc(c.case_id)}">${esc(c.case_id)}</b>`+
    `<span class="job-case-state mut">${esc(GEN_CASE_STATUS_ZH[c.status]||c.status)} · ${c.attempts||0} 次</span>`+
    `</div>`+
    (c.error?`<div class="job-case-error mut" title="${esc(c.error)}">${esc(c.error).slice(0,180)}</div>`:'')+
    `</div>`).join('');
}

// ---------------- 渲染 ----------------
function render(){
  if(!STATE)return;
  if(STATE.dims)DIMS=STATE.dims;
  if(STATE.dim_zh)ZH=STATE.dim_zh;
  document.getElementById('backendBadge').textContent='backend: '+STATE.backend;
  document.getElementById('sessBadge').textContent='会话 '+STATE.session_id+' · '+STATE.product_id;
  const v0StrategyLabel=STATE.v0_strategy==='llm_scratch'
    ?'LLM 从零起草（需求 + Rubric）'
    :'从基础 Skill 开始';
  document.getElementById('genRationale').innerHTML='<b>V0 起草方式：</b>'+esc(v0StrategyLabel)+'<br><b>生成依据：</b><br>'+esc(STATE.gen_rationale||'').replace(/\n/g,'<br>');
  ['dataCard','rubricCard','skillCard','realRunCard','outputCard'].forEach(id=>document.getElementById(id).classList.add('active'));

  // 版本 pills
  const pills=STATE.versions.map((v,i)=>{
    const cur=v.version===STATE.current_version?'cur':'';
    const rej=v.adopted?'':'rej';
    const cs=v.candidate_state?(' ['+v.candidate_state+']'):'';
    return `<span class="ver-pill ${cur} ${rej}" title="${(v.changelog||'').replace(/"/g,'')}">${v.version}${v.adopted?'':' (拒)'}${cs}</span>`;
  }).join('');
  const stop=STATE.optimizer_stop||{};
  const stopInfo=stop.enabled
    ?`<div class="small ${stop.stopped?'ok-txt':'mut'}" style="margin-top:6px">实验停止条件：overall ≥ ${fmt(stop.overall_target,1)}，或连续 ${stop.max_no_improvement} 版无提升；当前最佳 ${fmt(stop.best_overall,2)}，连续无提升 ${stop.no_improvement_streak}/${stop.max_no_improvement}${stop.stopped?' · 已停止：'+esc(stop.reason):''}</div>`
    :'';
  document.getElementById('versionPills').innerHTML = pills +
    `<div class="small mut" style="margin-top:6px">数据 ${STATE.n_cases} 条 ${JSON.stringify(STATE.splits)}</div>`+
    stopInfo;
  const advanceAction=STATE.actions&&STATE.actions.advance;
  const llmMode=STATE.optimizer_mode==='llm_rewrite';
  const optimizerControls=document.getElementById('optimizerLlmControls');
  if(optimizerControls)optimizerControls.style.display=llmMode?'flex':'none';
  const advBtn=document.getElementById('advanceBtn');
  advBtn.textContent=llmMode?'✍️ LLM 改写下一版（自由改写 + 自动 gate）':'▶ 生成下一版 skill（optimizer + gate）';
  advBtn.disabled=!(advanceAction?advanceAction.enabled:STATE.can_advance)||generationActive()||JUDGE_RUNNING;
  if(stop.stopped){
    document.getElementById('advanceMsg').innerHTML='<span class="ok-txt">■ 优化 loop 已停止</span> '+esc(stop.reason);
  }

  // pending 候选提示条（llm_rewrite 异步 gate）
  const pc=STATE.pending_candidate;
  const pcBanner=pc?`<div class="small warn-txt" style="margin-top:6px">🧪 待验证候选 <b>${pc.version}</b>（父 ${pc.parent||'—'}）：请对该候选跑 WB 生成 + 批量真实 Judge，判分完成后平台自动结算采纳/回滚。</div>`:'';

  // 当前 skill
  const cv=STATE.versions.find(v=>v.version===STATE.current_version);
  let skillBody;
  if(llmMode||(cv.proposal&&cv.proposal.target==='instructions_freeform')){
    const prose=cv.instructions_prose||(cv.proposal&&cv.proposal.instructions_text)||'';
    const contract=cv.requirement_contract||'';
    skillBody=`
    <div class="kv"><span>版本</span><b>${cv.version}</b> <span class="mut">(freeform · LLM 改写)</span></div>
    <div class="kv"><span>父版本</span><span>${cv.parent||'—'}</span></div>
    <div class="small mut" style="margin:6px 0">${cv.changelog||''}</div>
    ${pcBanner}
    ${cv.verdict?`<div class="small">gate 判定：<b>${cv.verdict}</b> — ${(cv.verdict_reasons&&cv.verdict_reasons.message)||''}</div>`:''}
    ${cv.proposal?`<details><summary>本版改写理由（rationale）</summary><pre>${JSON.stringify({change_summary:cv.proposal.change_summary,targets_failures:cv.proposal.targets_failures,preserved:cv.proposal.preserved,hypothesis:cv.proposal.hypothesis,self_check_no_hack:cv.proposal.self_check_no_hack},null,2)}</pre></details>`:''}
    ${contract?`<details open><summary>查看冻结任务契约（需求 + Rubric）</summary><pre style="white-space:pre-wrap">${esc(contract)}</pre></details>`:''}
    ${prose?`<details><summary>查看可改写质量规则</summary><pre style="white-space:pre-wrap">${esc(prose)}</pre></details>`:''}`;
  }else{
    const onDir=cv.directives_on.length?cv.directives_on.map(d=>`<span class="chip on">${d}</span>`).join(''):'<span class="mut">（全部关闭，等待优化打开）</span>';
    skillBody=`
    <div class="kv"><span>版本</span><b>${cv.version}</b></div>
    <div class="kv"><span>父版本</span><span>${cv.parent||'—'}</span></div>
    <div class="small mut" style="margin:6px 0">${cv.changelog||''}</div>
    ${pcBanner}
    <div class="mut small">已打开的 directive（优化动作 L1）：</div><div>${onDir}</div>
    ${cv.proposal?`<details><summary>本版来自的优化提议</summary><pre>${JSON.stringify(cv.proposal,null,2)}</pre></details>`:''}`;
  }
  document.getElementById('skillView').innerHTML=skillBody+
    `<details><summary>查看版本结构策略</summary><pre>${JSON.stringify(cv_structure(),null,1)}</pre></details>`;

  renderCurve(); renderFail(); renderRubric(); renderRubricEditor(); renderHistory(); renderOutputCard(); renderGenerationPanel();
}

function renderOutputCard(){
  const sel=document.getElementById('outCaseSel');
  if(!STATE.current_eval){sel.innerHTML='<option value="">导入数据后可选</option>';
    document.getElementById('checkPanel').innerHTML='';document.getElementById('reportPreview').textContent='';
    renderJudgeStatus();return;}
  const prev=sel.value;
  sel.innerHTML=STATE.current_eval.map(r=>{
    const has=r.report_text?' ✓报告':'';
    const hj=(r.check_judge&&Object.keys(r.check_judge).length)?' ✓judge':'';
    return `<option value="${r.case_id}">${r.case_id} (${r.split})${has}${hj}</option>`;
  }).join('');
  if(prev)sel.value=prev;
  sel.onchange=renderCheckPanel;
  renderCheckPanel();
  renderJudgeStatus();
}

const CHK_ZH={1:'满足',0.5:'部分',0:'不满足'};
function renderCheckPanel(){
  const cid=document.getElementById('outCaseSel').value;
  const el=document.getElementById('checkPanel'), pv=document.getElementById('reportPreview');
  if(!STATE.current_eval||!cid){el.innerHTML='';pv.textContent='';return;}
  const r=STATE.current_eval.find(x=>x.case_id===cid); if(!r){el.innerHTML='';return;}
  pv.innerHTML = r.report_text ? ('📄 报告已导入('+r.report_text.length+'字)') : '<span class="warn-txt">尚未导入报告</span>';
  const rb=STATE.rubric, jc=r.check_judge||{}, jr=r.check_judge_reason||{};
  let h='';
  rb.dimensions.forEach(d=>{
    const dj=(r.dims_judge||{})[d.name];
    h+=`<div style="margin:8px 0 2px"><b>${d.name_zh}</b> <span class="mut">模型 Judge ${dj!=null?dj:'—'}</span></div>`;
    (d.checks||[]).forEach(c=>{
      const jv=jc[c.id], jvt=(jv==null?'—':CHK_ZH[jv]);
      h+=`<div class="kv" title="${(c.desc||'').replace(/"/g,'&quot;')}"><span>· ${c.label}${c.redline?' <span class="flag">红线</span>':''}</span>`+
         `<span class="mut" title="${(jr[c.id]||'').replace(/"/g,'&quot;')}">${jvt}</span></div>`;
    });
  });
  if(!Object.keys(jc).length){
    h='<div class="mut">该 case 尚未完成模型 Judge。</div>'+h;
  }
  el.innerHTML=h;
}

function renderJudgeStatus(){
  const el=document.getElementById('judgeStatus');
  const btn=document.getElementById('runJudgeBtn');
  if(!el||!btn)return;
  const p=STATE&&STATE.judge_progress;
  const allReports=!!(p&&p.total_cases&&p.reports_ready===p.total_cases);
  const action=STATE&&STATE.actions&&STATE.actions.run_judge;
  const parallelInput=document.getElementById('judgeParallel');
  if(parallelInput&&GEN_CONFIG){
    if(!parallelInput.dataset.initialized){
      parallelInput.value=GEN_CONFIG.judge_parallel;
      parallelInput.dataset.initialized='1';
    }
    parallelInput.disabled=generationActive()||JUDGE_RUNNING;
  }
  btn.disabled=!(action?action.enabled:allReports)||generationActive()||JUDGE_RUNNING;
  btn.textContent=JUDGE_RUNNING
    ?'批量 Judge 执行中…'
    :(p&&p.judged_cases?'▶ 继续 Judge 未完成 case':'▶ 批量 Judge 全部 case');
  if(!p){
    el.innerHTML='<span class="mut">导入数据后显示 Judge 状态。</span>';return;
  }
  const complete=p.complete;
  el.innerHTML=`<div class="kv"><span>报告已就绪</span><span>${p.reports_ready}/${p.total_cases}</span></div>`+
    `<div class="kv"><span>模型 Judge</span><b class="${complete?'ok-txt':p.judged_cases?'warn-txt':'mut'}">${p.judged_cases}/${p.total_cases}</b></div>`+
    (JUDGE_SUMMARY?`<div class="kv"><span>最近调用</span><span>${esc(JUDGE_SUMMARY.llm_backend==='workbuddy'?'WorkBuddy CLI':'API')} · ${esc(JUDGE_SUMMARY.model||'—')}</span></div>`:'')+
    (!allReports?`<div class="warn-txt" style="margin-top:5px">仍缺 ${p.total_cases-p.reports_ready} 份报告，请先重试 WB CLI 或手工补齐。</div>`:'')+
    (complete?'<div class="ok-txt" style="margin-top:5px">全部 case 已完成模型 Judge，可以生成下一版 Skill。</div>':'')+
    (action&&!action.enabled&&action.reason&&!complete?`<div class="small mut" style="margin-top:5px">${esc(action.reason)}</div>`:'')+
    (JUDGE_SUMMARY&&JUDGE_SUMMARY.failed_cases
      ?`<div class="warn-txt" style="margin-top:5px">最近一次批量 Judge 有 ${JUDGE_SUMMARY.failed_cases} 个 case 未成功，可再次点击整批重跑。</div>`+
       JUDGE_RESULTS.filter(x=>x.status!=='judged').map(x=>
         `<div class="mut">${esc(x.case_id)}：${esc(x.error||x.status)}</div>`).join('')
      :'');
}
function cv_structure(){
  return {
    baseline:"skills/research-report",
    policy:"SKILL.md 与目录结构保持基线不变",
    evolution:"仅在 references/instructions.md 累积 optimizer directive"
  };
}

function renderCurve(){
  const el=document.getElementById('curveView');
  const jp=STATE.judge_progress;
  const waitingJudge=!!(jp&&jp.required&&!jp.complete);
  if(!STATE.curve||!STATE.curve.length||!STATE.curve[0].dev){
    el.innerHTML=waitingJudge
      ?`<span class="mut">当前版本等待批量模型 Judge（${jp.judged_cases}/${jp.total_cases}），完成后显示首版真实分数。</span>`
      :'<span class="mut">导入数据后显示</span>';
    return;
  }
  let h=waitingJudge
    ?`<div class="small mut" style="margin-bottom:6px">当前版本正在等待模型 Judge（${jp.judged_cases}/${jp.total_cases}）；下表继续显示此前已完成 Judge 的版本。</div>`
    :'';
  h+='<table><tr><th>版本</th>'+DIMS.map(d=>`<th>${ZH[d]}</th>`).join('')+'<th>overall</th><th>红线</th></tr>';
  STATE.curve.forEach(pt=>{
    const dev=pt.dev,test=pt.test;
    const gateRejected=pt.candidate_state==='rejected'||pt.verdict==='rejected';
    const status=gateRejected?'<span class="warn-txt">（Gate 拒绝，未采纳）</span>':'';
    let tds='';DIMS.forEach(d=>{const dv=dev[d],tv=test?test[d]:null;tds+=`<td>${fmt(dv,1)}${tv!=null?'<span class="mut">/'+fmt(tv,1)+'</span>':''}</td>`;});
    h+=`<tr><td><b>${esc(pt.version)}</b>${status}</td>${tds}<td><b>${fmt(dev.overall,2)}</b>${test?'<span class="mut">/'+fmt(test.overall,2)+'</span>':''}</td><td>${dev.red_line_fails||0}</td></tr>`;
  });
  h+='</table><div class="small mut" style="margin-top:4px">D=dev / <span class="mut">T=test(held-out)</span> · “Gate 拒绝，未采纳”表示该版已完成 Judge，但因维度回退等门禁条件未被采纳；后续仍从最佳采纳版生成</div>';
  // 目标行
  const t=STATE.target;
  h+=`<div class="small" style="margin-top:8px">目标：${DIMS.map(d=>`${ZH[d]}≥${t[d]}`).join(' · ')} · overall≥${t.overall}</div>`;
  el.innerHTML=h;
}

function renderFail(){
  const el=document.getElementById('failView'); const f=STATE.current_failures;
  const jp=STATE.judge_progress;
  if(jp&&jp.required&&!jp.complete){
    el.innerHTML='<span class="mut">批量模型 Judge 完成后再生成失败聚类。</span>';return;
  }
  if(!f){el.innerHTML='<span class="mut">导入数据后显示</span>';return;}
  if(!f.length){el.innerHTML='<span class="ok-txt">当前无失败聚类 🎉</span>';return;}
  el.innerHTML=f.map(p=>`<div style="margin-bottom:8px">
    <div><b class="${p.severity==='high'?'warn-txt':''}">${p.pattern}</b> <span class="chip">${p.hit_count}命中</span> <span class="chip">${p.severity}</span></div>
    <div class="small mut">影响：${p.affected_dims.map(d=>ZH[d]||d).join('/')} ${p.directive_hint?'· 可修：'+p.directive_hint:'· <span class="warn-txt">无指令级修法(结构性)</span>'}</div>
  </div>`).join('');
}

function rubricDimsHtml(rb){
  // 六维展开(判据 + 目标 + 检查点), 左/中/右三处复用
  return rb.dimensions.map(d=>`<div style="margin:6px 0">
      <div class="kv"><span>${d.name_zh} ${d.is_reverse?'<span class="chip">反向</span>':''}</span><span>权重 ${d.weight}${d.hard_floor?' · 红线<'+d.hard_floor:''}${rb.target&&rb.target[d.name]!=null?' · <b class="ok-txt">目标≥'+rb.target[d.name]+'</b>':''}</span></div>
      <div class="small mut" title="${(d.criteria||'').replace(/"/g,'')}">${d.criteria}</div>
      ${d.checks?'<div class="small" style="margin:4px 0 2px 8px">'+d.checks.map(c=>`<div class="kv" title="${(c.desc||'').replace(/"/g,'')}"><span>· ${c.label}${c.redline?' <span class="flag">红线</span>':''}</span><span class="mut">${c.effect||''}</span></div>`).join('')+'</div>':''}
    </div>`).join('');
}

function renderRubric(){
  const rb=STATE.rubric; const el=document.getElementById('rubricView');
  el.innerHTML=`<div class="kv"><span>rubric 版本</span><b>${rb.version}</b></div>`+
    rubricDimsHtml(rb)+
    `<div class="kv" style="margin-top:6px"><span>overall 目标</span><b class="ok-txt">≥${(rb.target&&rb.target.overall)||'-'}</b></div>`+
    `<details><summary>gates</summary><pre>${JSON.stringify(rb.gates,null,1)}</pre></details>`;
}

function renderRubricEditor(){
  const rb=STATE.rubric; const el=document.getElementById('rubricEditor');
  el.innerHTML=rb.dimensions.map(d=>`<div class="row"><span style="flex:1">${d.name_zh}</span>
     <input class="w" data-dim="${d.name}" value="${d.weight}" style="width:70px" type="number" step="0.05" min="0" max="1"></div>`).join('')+
     `<div class="row" style="margin-top:6px"><span style="flex:1">overall 目标</span>
     <input class="tgt-overall" value="${rb.target.overall}" style="width:70px" type="number" step="0.1"></div>`;
  document.getElementById('rubricVer').textContent='当前：'+rb.version;
  const erb=document.getElementById('editorRubricBody'); if(erb)erb.innerHTML=rubricDimsHtml(rb);
}

const EV_ZH={created:"生成 V0",import_data:"导入数据",
  edit_rubric:"编辑 rubric",version_adopted:"采纳新版",version_rejected:"版本被拒",
  converged:"收敛/平台期",import_output:"导入报告文本",import_judgment:"导入LLM评分",
  generation_import:"WB 批量导入",run_judge_batch:"批量模型 Judge"};
// ---- 打开已有会话 ----
async function loadSessions(){
  try{
    const j=await api('/api/sessions','GET');
    const sel=document.getElementById('sessSel');
    const list=j.sessions||[];
    sel.innerHTML='<option value="">（选择会话）</option>'+list.map(s=>
      `<option value="${s.id}">${s.id} · ${s.product_id} · ${s.n_cases}案 · ${s.current_version}</option>`).join('');
    document.getElementById('sessListInfo').textContent='共 '+list.length+' 个已落盘会话';
  }catch(e){}
}
async function openSession(id){
  if(!id)return;
  const j=await api('/api/session?id='+encodeURIComponent(id),'GET');
  SID=id; STATE=j; GEN_JOB=null; JUDGE_SUMMARY=null; JUDGE_RESULTS=[]; JUDGE_RUNNING=false;
  render(); await loadLatestGeneration(); toast('已打开会话 '+id);
}
document.getElementById('openSessBtn').onclick=()=>openSession(document.getElementById('sessSel').value);
document.getElementById('refreshSessBtn').onclick=loadSessions;

// 初始化: 载入会话列表; URL 带 ?id= 时自动打开
(async()=>{
  try{
    const me=await api('/api/me','GET');
    document.getElementById('userBadge').textContent='👤 '+(me.display_name||me.login_name);
  }catch(e){return;}   // 401 已由 authWall 拦截整页
  try{
    GEN_CONFIG=await api('/api/generation/config','GET');
  }catch(e){
    GEN_CONFIG={
      ready:false,
      error:e.status===404
        ?'当前后端版本过旧：请停止并重新启动 server.py'
        :('无法读取 WB 运行配置：'+(e.message||'未知错误'))
    };
  }
  renderGenerationPanel();
  await loadSessions();
  const qid=new URLSearchParams(location.search).get('id');
  if(qid){ document.getElementById('sessSel').value=qid; openSession(qid); }
})();

function renderHistory(){
  const el=document.getElementById('historyView');
  const h=STATE.history||[];
  if(!h.length){el.innerHTML='<span class="mut">尚无记录</span>';return;}
  el.innerHTML='<div class="small mut" style="margin-bottom:6px">共 '+h.length+' 条 · 每次变更即追加落盘</div>'+
    h.slice().reverse().map(e=>{
      const t=new Date(e.ts*1000);const hh=('0'+t.getHours()).slice(-2)+':'+('0'+t.getMinutes()).slice(-2)+':'+('0'+t.getSeconds()).slice(-2);
      let detail='';const p=e.payload||{};
      if(e.type==='import_data')detail=p.n_cases+' 条 '+JSON.stringify(p.splits);
      else if(e.type==='edit_rubric')detail='→ '+p.new_version;
      else if(e.type==='version_adopted')detail=p.version+' ('+(p.directives_on||[]).slice(-1)+')';
      else if(e.type==='version_rejected')detail=p.version+' · '+p.reason;
      else if(e.type==='created')detail=p.product_id;
      else if(e.type==='converged')detail=p.at_version;
      else if(e.type==='import_output')detail=p.case_id+' ('+p.n_chars+'字)';
      else if(e.type==='import_judgment')detail=p.case_id+' '+JSON.stringify(p.scores);
      else if(e.type==='generation_import')detail=p.version+' · '+p.n_cases+' case';
      return `<div class="kv"><span><span class="mut">${hh}</span> ${EV_ZH[e.type]||e.type}</span><span class="mut">${detail}</span></div>`;
    }).join('');
}
