const state={context:null,config:null,session:null,report:null,batch:null,candidate:null,experiment:null,draft:null,iterationHistory:null,memory:null,validationSelection:null,selectedQuote:'',selectedRenderedQuote:'',selectionRequestId:0,editingFeedbackId:'',classificationRunning:false};
const $=id=>document.getElementById(id);
function esc(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function toast(message){const el=$('toast');el.textContent=message;el.style.display='block';clearTimeout(el._timer);el._timer=setTimeout(()=>el.style.display='none',3200);}
async function api(path,method='GET',body){const options={method,headers:{Accept:'application/json'}};if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}const response=await fetch(path,options);const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.error||`${response.status} ${response.statusText}`);return data;}

function renderMarkdown(value){
  const inline=text=>esc(text).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank">$1</a>');
  const lines=String(value||'').replace(/\r/g,'').split('\n'),out=[];let index=0,list=null;
  const closeList=()=>{if(list){out.push('</'+list+'>');list=null;}};
  while(index<lines.length){let line=lines[index];
    if(line.startsWith('```')){closeList();const code=[];index++;while(index<lines.length&&!lines[index].startsWith('```'))code.push(lines[index++]);out.push('<pre><code>'+esc(code.join('\n'))+'</code></pre>');index++;continue;}
    if(/^\s*\|.*\|\s*$/.test(line)&&index+1<lines.length&&/^\s*\|?\s*:?-+/.test(lines[index+1])){closeList();const rows=[];rows.push(line);index+=2;while(index<lines.length&&/^\s*\|.*\|\s*$/.test(lines[index]))rows.push(lines[index++]);const cells=row=>row.trim().replace(/^\||\|$/g,'').split('|').map(cell=>inline(cell.trim()));out.push('<div class="md-table"><table><thead><tr>'+cells(rows[0]).map(cell=>'<th>'+cell+'</th>').join('')+'</tr></thead><tbody>'+rows.slice(1).map(row=>'<tr>'+cells(row).map(cell=>'<td>'+cell+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>');continue;}
    const heading=line.match(/^(#{1,4})\s+(.+)$/);if(heading){closeList();out.push('<h'+heading[1].length+'>'+inline(heading[2])+'</h'+heading[1].length+'>');index++;continue;}
    if(/^>\s?/.test(line)){closeList();out.push('<blockquote>'+inline(line.replace(/^>\s?/,''))+'</blockquote>');index++;continue;}
    const bullet=line.match(/^\s*[-*+]\s+(.+)$/),ordered=line.match(/^\s*\d+[.)]\s+(.+)$/);
    if(bullet||ordered){const wanted=ordered?'ol':'ul';if(list!==wanted){closeList();list=wanted;out.push('<'+list+'>');}out.push('<li>'+inline((bullet||ordered)[1])+'</li>');index++;continue;}
    if(!line.trim()){closeList();index++;continue;}
    closeList();const paragraph=[line.trim()];index++;while(index<lines.length&&lines[index].trim()&&!/^(#{1,4})\s+|^>|^\s*[-*+]\s+|^\s*\d+[.)]\s+|^```|^\s*\|.*\|\s*$/.test(lines[index]))paragraph.push(lines[index++].trim());out.push('<p>'+inline(paragraph.join(' '))+'</p>');
  }
  closeList();return out.join('');
}

function currentSession(){return state.context?.sessions?.find(item=>item.session_id===$('sessionSelect').value)||null;}
function caseLabel(session,caseId){const item=(session.cases||[]).find(row=>row.case_id===caseId)||{};return item.topic||item.metadata?.display_name||caseId;}
function caseFileName(session,caseId){const item=(session.cases||[]).find(row=>row.case_id===caseId)||{};return item.metadata?.source_file||item.metadata?.display_name||item.topic||caseId;}
function sortCaseIdsByFileName(session,caseIds){return (caseIds||[]).slice().sort((left,right)=>String(caseFileName(session,right)).localeCompare(String(caseFileName(session,left)),'zh-CN',{numeric:true,sensitivity:'base'}));}
function reportRef(){if(!state.report)return null;return {session_id:state.report.session_id,skill_version:state.report.skill_version,case_id:state.report.case_id,report_sha256:state.report.report_sha256,rubric_sha256:state.report.rubric_sha256};}

function renderSession(){
  const session=currentSession();state.session=session;
  const source=session?.rubric_source||{};
  const sourceLabels={
    imported:source.filename||'已导入文件',
    default:source.filename||'默认 Rubric',
    session_snapshot:'Session 冻结快照',
    edited:'Session 内编辑版本',
  };
  const sourceLabel=sourceLabels[source.kind]||source.filename||'Session 冻结 Rubric';
  $('rubricSummary').innerHTML=session?`<span>Rubrics ${esc(session.rubric_version||'未知版本')}</span><span title="版本校验指纹：${esc(session.rubric_sha256)}">${esc(sourceLabel)}</span>`:'';
  if(!session){$('versionTree').innerHTML='<div class="empty">选择 Session 后查看 Skill 版本与报告</div>';return;}
  $('versionTree').innerHTML=(session.versions||[]).slice().reverse().map(version=>{
    const id=version.version,caseIds=session.version_cases?.[id]||[];
    const sortedCaseIds=sortCaseIdsByFileName(session,caseIds);
    return `<details class="version-item"><summary>${esc(id)} · ${caseIds.length} Cases</summary>${sortedCaseIds.map(caseId=>`<button class="case-button" data-version="${esc(id)}" data-case="${esc(caseId)}"><b>${esc(caseLabel(session,caseId))}</b><small>${esc(caseId)}</small></button>`).join('')||'<div class="empty">没有可读取报告</div>'}</details>`;
  }).join('')||'<div class="empty">没有生成记录</div>';
  document.querySelectorAll('.case-button').forEach(button=>button.onclick=()=>openReport(session.session_id,button.dataset.version,button.dataset.case));
}

async function loadContext(preferredSession=''){
  $('status').textContent='同步中…';
  const [context,config]=await Promise.all([api('/api/rubrics-loop/context'),api('/api/generation/config')]);state.context=context;state.config=config;
  const memoryUsers=context.memory_users||['local','tianliu','angill','sijing','zoe'];$('memoryUser').innerHTML=memoryUsers.map(user=>`<option value="${esc(user)}">${esc(user)}</option>`).join('');$('memoryUser').value=context.default_memory_user||'local';
  const requested=preferredSession||new URLSearchParams(location.search).get('session');
  $('sessionSelect').innerHTML='<option value="">选择一个 Session 查看报告…</option>'+(context.sessions||[]).map(item=>`<option value="${esc(item.session_id)}">${esc(item.session_id)} · ${esc(item.rubric_version||'')}</option>`).join('');
  $('sessionSelect').value=requested&&context.sessions.some(item=>item.session_id===requested)?requested:'';
  setupModelSelectors();renderSession();$('status').textContent=`${context.sessions.length} Sessions`;
  const query=new URLSearchParams(location.search),version=query.get('skill_version')||query.get('version'),caseId=query.get('case_id');
  if(requested&&version&&caseId)await openReport(requested,version,caseId,query.get('report_sha256')||'',query.get('rubric_sha256')||'');
  await restoreWorkflow();
  await refreshMemory();
}

async function openReport(sessionId,version,caseId,reportHash='',rubricHash=''){
  try{
    const query=new URLSearchParams({session_id:sessionId,skill_version:version,case_id:caseId});if(reportHash)query.set('report_sha256',reportHash);if(rubricHash)query.set('rubric_sha256',rubricHash);
    state.report=await api('/api/rubrics-loop/report?'+query);
    $('reportTitle').textContent=caseLabel(currentSession()||{},caseId);
    $('currentReportName').textContent=caseLabel(currentSession()||{},caseId);
    $('currentReportTools').classList.remove('hidden');
    const reportMeta=[
      {text:`Session ${sessionId}`},
      {text:`Skill ${version}`},
      {text:`Case ${caseId}`},
      {text:`Rubrics ${state.report.rubric_version||'历史版本不可用'}`,title:`Rubrics 版本校验指纹：${state.report.rubric_sha256}`},
      {text:'报告快照已锁定',title:`报告版本校验指纹：${state.report.report_sha256}`},
    ];
    $('reportMeta').innerHTML=reportMeta.map(item=>`<span${item.title?` title="${esc(item.title)}"`:''}>${esc(item.text)}</span>`).join('');
    renderJudgeScore(state.report.judge);
    $('report').innerHTML=renderMarkdown(state.report.report_text);$('reportHint').textContent=state.report.annotatable?'选中报告原文，右侧会出现批注输入框。':'该报告对应的历史 Rubrics Snapshot 不可用，暂不能批注。';
    $('saveFeedback').disabled=!state.report.annotatable;
    clearSelection();
    document.querySelectorAll('.case-button').forEach(button=>{const active=button.dataset.version===version&&button.dataset.case===caseId;button.classList.toggle('active',active);if(active)button.closest('details').open=true;});
  }catch(error){toast(error.message);}
}

function renderJudgeScore(judge){
  const el=$('judgeScore');el.classList.remove('hidden','stale');
  if(!judge){el.innerHTML='<div class="judge-status">Judge：当前报告尚无评分</div>';return;}
  const dimensions=(judge.dimensions||[]).map(item=>`<div class="judge-dimension"><small>${esc(item.label)}</small><b>${Number(item.score).toFixed(2)}</b></div>`).join('');
  el.innerHTML=`<div class="judge-overall"><small>Judge 总分</small><b>${Number(judge.overall).toFixed(2)}</b></div>${dimensions}`;
}

function clearSelection(){state.selectionRequestId+=1;state.selectedQuote='';state.selectedRenderedQuote='';$('selectionPreview').classList.add('hidden');$('selectedQuote').textContent='';$('feedbackScope').disabled=false;$('feedbackScope').value='current';$('feedbackModeLabel').textContent='反馈内容';$('saveFeedback').disabled=!state.report?.annotatable;}

function statusLabel(value){return {draft:'草稿',submitted:'待处理',optimizing:'Optimizer 运行中',completed:'候选 Rubrics 已生成',validated:'待审核',staged:'已暂存',collecting:'累计中',validating:'验证中',running:'验证中',awaiting_review:'待决策',adopted:'已采纳',rejected:'已拒绝',created:'待启动',queued:'排队中',failed:'失败'}[value]||value||'未知';}
function timeLabel(value){if(!value)return'';return new Date(Number(value)*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}
function candidateModelLabel(candidate){const config=candidate?.model_config||{};return `${backendLabel(config.llm_backend)} · ${config.llm_model||'默认模型'}${config.llm_reasoning_effort?` · ${config.llm_reasoning_effort}`:''}`;}
function historyCandidateStatus(candidate){const cumulative=candidate?.cumulative_validation;if(cumulative?.included&&!cumulative.is_latest_revision){if(['queued','running'].includes(cumulative.experiment_status))return'已纳入累计验证';if(cumulative.experiment_status==='completed')return'已随累计草案验证';if(cumulative.experiment_status==='failed')return'累计验证失败';}return statusLabel(candidate?.status);}
function iterationStatusLabel(iteration,candidate){if(candidate)return historyCandidateStatus(candidate);const routing=iteration.routing_summary||{};if(iteration.batch_status==='completed'&&routing.memory_count&&!routing.rubric_count)return'已完成 · Memory';return statusLabel(iteration.batch_status);}
function selectedMemoryUser(){return $('memoryUser').value||'local';}
function clearCandidateView(){state.candidate=null;state.experiment=null;state.validationSelection=null;$('candidatePanel').classList.add('hidden');$('candidateEmpty').classList.remove('hidden');$('candidateView').classList.add('hidden');$('experimentPanel').classList.add('hidden');$('optimizerRunStatus').classList.add('hidden');renderHistory();}
async function openIteration(batchId,candidateId='',experimentId='',scroll=true,selectedBatchIds=null){
  const session=currentSession();if(!session)return;
  try{
    const requests=[api('/api/rubrics-loop/batches?'+new URLSearchParams({session_id:session.session_id,batch_id:batchId}))];
    if(candidateId)requests.push(api('/api/rubrics-loop/candidate?'+new URLSearchParams({session_id:session.session_id,candidate_id:candidateId})));
    if(experimentId)requests.push(api('/api/rubrics-loop/experiment?'+new URLSearchParams({session_id:session.session_id,experiment_id:experimentId})));
    const values=await Promise.all(requests);state.batch=values[0];if(state.batch.memory_user&&$('memoryUser').querySelector(`option[value="${CSS.escape(state.batch.memory_user)}"]`))$('memoryUser').value=state.batch.memory_user;state.candidate=candidateId?values[1]:null;state.experiment=experimentId?values[values.length-1]:null;state.validationSelection=selectedBatchIds;state.editingFeedbackId='';renderBatch();refreshMemory();
    if(state.candidate){renderCandidate(scroll);renderHistory();}else clearCandidateView();
    if(state.experiment){$('experimentPanel').classList.remove('hidden');renderExperiment();if(['queued','running'].includes(state.experiment.status))pollExperiment();}else $('experimentPanel').classList.add('hidden');
    if(scroll)$('candidatePanel').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(error){toast(error.message);}
}
async function restoreWorkflow(){
  const session=currentSession();
  try{
    if(!session){state.iterationHistory=await loadAllIterationHistory();state.draft=null;renderHistory();return;}
    state.iterationHistory=await api('/api/rubrics-loop/iterations?session_id='+encodeURIComponent(session.session_id));state.draft=state.iterationHistory.active_draft||null;renderHistory();
    const active=state.iterationHistory.active||{};
    const query=new URLSearchParams(location.search),requestedCandidate=query.get('candidate_id')||'';
    if(requestedCandidate){
      const requestedIteration=(state.iterationHistory.groups||[]).flatMap(group=>group.iterations||[]).find(iteration=>(iteration.candidates||[]).some(candidate=>candidate.candidate_id===requestedCandidate));
      if(requestedIteration){await openIteration(requestedIteration.batch_id,requestedCandidate,requestedIteration.latest_experiment_id||'',true);return;}
    }
    if(active.batch_id&&!active.candidate_id){await openIteration(active.batch_id,'','',false);return;}
    state.batch=null;renderBatch();clearCandidateView();
  }catch(error){console.warn(error);toast('历史记录恢复失败：'+error.message);}
}

async function loadAllIterationHistory(){
  try{return await api('/api/rubrics-loop/iterations');}
  catch(error){
    const histories=await Promise.all((state.context?.sessions||[]).map(async session=>{try{return {session,history:await api('/api/rubrics-loop/iterations?session_id='+encodeURIComponent(session.session_id))};}catch(innerError){return null;}}));
    return {sessions:histories.filter(Boolean).map(({session,history})=>{const groups=history.groups||[],experiments=groups.flatMap(group=>(group.iterations||[]).flatMap(iteration=>iteration.experiments||[])),draft=history.active_draft,updatedAt=Math.max(0,...groups.map(group=>group.updated_at||0),draft?.updated_at||0);return {session_id:session.session_id,rubric_version:session.rubric_version,updated_at:updatedAt,iteration_count:groups.reduce((count,group)=>count+(group.iterations||[]).length,0),active_experiment_count:experiments.filter(item=>['queued','running'].includes(item.status)).length,groups,active_draft:draft};}).filter(item=>item.iteration_count||item.active_draft).sort((left,right)=>(right.updated_at||0)-(left.updated_at||0))};
  }
}

function historyValidationBuckets(group){
  const iterations=group.iterations||[],byBatch=new Map(iterations.map(item=>[item.batch_id,item])),assigned=new Set(),buckets=[];
  const noRubric=iterations.filter(item=>item.batch_status==='completed'&&item.routing_summary?.status==='completed'&&!item.routing_summary?.rubric_count);
  noRubric.forEach(item=>assigned.add(item.batch_id));
  const completed=iterations.flatMap(item=>item.experiments||[]).filter(item=>item.status==='completed').sort((left,right)=>(left.finished_at||left.updated_at||left.created_at||0)-(right.finished_at||right.updated_at||right.created_at||0));
  completed.forEach(experiment=>{
    const cohort=(experiment.included_batch_ids||[]).map(id=>byBatch.get(id)).filter(item=>item&&!assigned.has(item.batch_id));
    if(!cohort.length)return;
    cohort.forEach(item=>assigned.add(item.batch_id));
    const candidate=cohort.flatMap(item=>item.candidates||[]).find(item=>item.candidate_id===experiment.candidate_id);
    const kind=['adopted','rejected'].includes(candidate?.status)?'decided':'validated_pending_decision';
    buckets.push({kind,experiment,iterations:cohort.slice().sort((left,right)=>(right.created_at||0)-(left.created_at||0))});
  });
  const active=iterations.flatMap(item=>item.experiments||[]).filter(item=>['created','queued','running'].includes(item.status)).sort((left,right)=>(right.updated_at||0)-(left.updated_at||0))[0];
  if(active){
    const cohort=(active.included_batch_ids||[]).map(id=>byBatch.get(id)).filter(item=>item&&!assigned.has(item.batch_id));
    if(cohort.length){cohort.forEach(item=>assigned.add(item.batch_id));buckets.push({kind:'validating',experiment:active,iterations:cohort.slice().sort((left,right)=>(right.created_at||0)-(left.created_at||0))});}
  }
  const unvalidated=iterations.filter(item=>!assigned.has(item.batch_id));
  if(unvalidated.length)buckets.push({kind:'unvalidated',iterations:unvalidated});
  if(noRubric.length)buckets.push({kind:'no_rubric',iterations:noRubric});
  const priority={unvalidated:0,validating:1,validated_pending_decision:2,decided:3,no_rubric:4};
  return buckets.sort((left,right)=>priority[left.kind]-priority[right.kind]||((right.experiment?.finished_at||right.experiment?.updated_at||0)-(left.experiment?.finished_at||left.experiment?.updated_at||0)));
}

function historyIterationHtml(iteration,round,sessionId,selectedCandidateId=''){
    const candidate=iteration.candidates?.[0]||null,experiment=iteration.experiments?.find(item=>item.experiment_id===iteration.latest_experiment_id)||iteration.experiments?.[0]||null,selected=candidate?.candidate_id===selectedCandidateId;
    const feedbackPreview=(iteration.feedback||[]).map(item=>{const hasQuote=item.scope==='inline'&&item.quote;return `<li class="history-feedback-item${hasQuote?' has-quote':''}"${hasQuote?' tabindex="0"':''}><b>${esc({inline:'原文批注',report:'整篇反馈',batch:'共性意见'}[item.scope]||item.scope)}</b>${esc(item.content)}${hasQuote?`<span class="history-quote-tooltip" role="tooltip"><strong>当时选中的原文</strong><span>${esc(item.quote)}</span></span>`:''}</li>`;}).join('');
    const cumulative=candidate?.cumulative_validation,cumulativeNote=cumulative?.included&&!cumulative.is_latest_revision?`<small class="cumulative-validation-note">本轮修改已并入 ${esc(cumulative.draft_revision_count)} 轮累计 Rubrics，并由实验 ${esc(cumulative.experiment_id)} 一起验证。</small>`:'',routing=iteration.routing_summary||{},memoryOnly=iteration.batch_status==='completed'&&routing.memory_count&&!routing.rubric_count;
    const resultHtml=candidate?`<div class="history-candidate"><b>${esc(candidateModelLabel(candidate))}</b><span>修改 ${esc((candidate.modified_check_ids||[]).join('、')||'无 Check')}</span>${candidate.summary?`<p>${esc(candidate.summary)}</p>`:''}${cumulativeNote}${experiment?`<small>验证：${esc(statusLabel(experiment.status))}${experiment.acceptance_status?` · Rubrics 验收 ${esc(acceptanceStatusLabel(experiment.acceptance_status))}`:''}${experiment.experiment_session_id?` · ${esc(experiment.experiment_session_id)}`:''}</small>`:''}</div>`:memoryOnly?`<div class="history-memory-result"><b>本轮无需修改 Rubrics</b><span>${esc(routing.memory_saved_count||0)} 条已保存到 Memory，${esc(routing.memory_count||0)} 条归类为 Memory。</span></div>`:'<div class="empty">尚未生成 Candidate</div>';
    return `<details class="history-iteration${selected?' selected':''}"${selected?' open':''}><summary><span><b>第 ${round} 轮</b><small>${esc(timeLabel(iteration.created_at))} · ${iteration.report_count} 篇报告 · ${iteration.feedback_count} 条反馈</small></span><em>${esc(iterationStatusLabel(iteration,candidate))}</em></summary><div class="history-detail">${resultHtml}<details><summary>查看本轮反馈</summary><ul class="history-feedback">${feedbackPreview||'<li>暂无反馈</li>'}</ul></details><button data-history-session="${esc(sessionId)}" data-open-iteration="${esc(iteration.batch_id)}" data-candidate-id="${esc(iteration.latest_candidate_id||'')}" data-experiment-id="${esc(iteration.latest_experiment_id||'')}">${candidate?(selected?'正在审核':'查看候选 Rubrics'):memoryOnly?'查看本轮结果':'继续本轮'}</button></div></details>`;
}

function historyGroupsHtml(groups,sessionId,selectedCandidateId=''){
  return (groups||[]).map(group=>{
    const chronological=(group.iterations||[]).slice().sort((left,right)=>(left.created_at||0)-(right.created_at||0));
    const rounds=new Map(chronological.map((iteration,index)=>[iteration.batch_id,index+1]));
    const buckets=historyValidationBuckets(group).map(bucket=>{
      const count=bucket.iterations.length,date=timeLabel(bucket.experiment?.finished_at||bucket.experiment?.updated_at||bucket.experiment?.created_at),labels={unvalidated:`未验证 · 累计 ${count} 轮`,validating:`验证中 · 累计 ${count} 轮`,validated_pending_decision:`已验证待决策 · ${date} · ${count} 轮`,decided:`已决策 · ${date} · ${count} 轮`,no_rubric:`无需验证 · Memory ${count} 轮`};
      const selectable=bucket.kind==='unvalidated'?bucket.iterations.slice().sort((left,right)=>(left.created_at||0)-(right.created_at||0)).map(iteration=>({iteration,candidate:(iteration.candidates||[])[0]})).filter(item=>item.candidate&&['validated','staged','awaiting_review'].includes(item.candidate.status)):[];
      const picker=selectable.length?`<div class="validation-picker"><div><b>选择本次要验证的未验证轮次</b><small>默认全选；选择后面的轮次会自动包含此前依赖的修改。</small></div><div class="validation-round-options">${selectable.map(item=>`<label><input type="checkbox" checked data-validation-round="${esc(item.iteration.batch_id)}" data-validation-candidate="${esc(item.candidate.candidate_id)}" data-validation-experiment="${esc(item.iteration.latest_experiment_id||'')}" data-validation-included="${esc((item.candidate.feedback_batch_ids||[item.iteration.batch_id]).join(','))}">第 ${esc(rounds.get(item.iteration.batch_id))} 轮</label>`).join('')}</div><button data-prepare-selected-validation>验证所选轮次</button></div>`:'';
      const defaultOpen=['unvalidated','validating','validated_pending_decision'].includes(bucket.kind);
      return `<details class="history-validation-group ${esc(bucket.kind)}"${defaultOpen?' open':''}><summary><b>${esc(labels[bucket.kind])}</b>${bucket.experiment?.experiment_id?`<span>${esc(bucket.experiment.experiment_id)}</span>`:''}</summary><div>${picker}${bucket.iterations.map(iteration=>historyIterationHtml(iteration,rounds.get(iteration.batch_id),sessionId,selectedCandidateId)).join('')}</div></details>`;
    }).join('');
    return `<section class="history-group"><header><b>Rubrics ${esc(group.rubric_version)}</b><span>${group.iterations.length} 轮 · 最近 ${esc(timeLabel(group.updated_at))}</span></header><div>${buckets}</div></section>`;
  }).join('');
}

function renderMemory(){
  const memory=state.memory||{},l0=memory.writing_episodes||memory.pending_episodes||[],pending=memory.pending_episodes||[],l1=memory.l1_memories||[],l2=memory.l2_profiles||[],user=memory.user||selectedMemoryUser();
  $('memoryOwner').textContent=`当前查看：${user} · L0、L1 与 L2 分用户独立保存。`;
  $('memorySummary').innerHTML=`<span>L0 Episode ${l0.length} · 待观察 ${pending.length}</span><span>L1 稳定规则 ${l1.length}</span><span>L2 Profile ${l2.length}</span>`;
  const layer=(title,items,html)=>`<details class="memory-layer"><summary><b>${title}</b><span>${items.length} 条</span></summary><div>${items.length?items.map(html).join(''):'<div class="empty">暂无记录</div>'}</div></details>`;
  const episodeField=(label,value)=>value?`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`:'';
  const episodeHtml=item=>`<details class="memory-episode"><summary><span><b>${esc(item.feedback||'')}</b><small>${esc([item.audience,item.reportType,item.topic].filter(Boolean).join(' · '))}</small></span><em>${item.status==='promoted'?'已晋升 L1':'待观察'}</em></summary><dl>${episodeField('Episode ID',item.id)}${episodeField('任务',item.task)}${episodeField('Session',item.sessionId)}${episodeField('主题',item.topic)}${episodeField('受众',item.audience)}${episodeField('报告类型',item.reportType)}${episodeField('写作阶段',item.stage)}${episodeField('选中的报告原文',item.contextBefore)}${episodeField('用户反馈',item.feedback)}${episodeField('反馈后语境',item.contextAfter)}${episodeField('报告产物',item.finalArtifact)}${episodeField('来源标识',item.externalSourceId)}${episodeField('候选维度',(item.candidateDimensions||[]).join('、'))}${episodeField('关联 L1',(item.promotedL1Ids||[]).join('、'))}${episodeField('状态',item.status)}${episodeField('记录时间',item.createdAt)}${episodeField('更新时间',item.updatedAt)}</dl></details>`;
  $('memoryStore').innerHTML=layer('L0 · Writing Episodes',l0,episodeHtml)+layer('L1 · 稳定写作规则',l1,item=>`<article><b>${esc(item.rule||'')}</b><small>${esc([item.dimension,item.scope,item.scopeValue].filter(Boolean).join(' · '))}</small></article>`)+layer('L2 · 归纳 Profile',l2,item=>`<article><b>${esc(item.summary||'')}</b>${(item.rules||[]).length?`<ul>${item.rules.map(rule=>`<li>${esc(rule)}</li>`).join('')}</ul>`:''}<small>${esc([item.dimension,item.scope,item.scopeValue].filter(Boolean).join(' · '))}</small></article>`);
}
async function refreshMemory(){const user=selectedMemoryUser();$('memoryOwner').textContent=`正在读取 ${user} 的 Memory…`;try{state.memory=await api('/api/rubrics-loop/memory?user='+encodeURIComponent(user));renderMemory();}catch(error){state.memory=null;$('memorySummary').innerHTML='<span>读取失败</span>';$('memoryStore').innerHTML=`<div class="empty">${esc(error.message)}</div>`;}}

function bindHistoryActions(){
  document.querySelectorAll('[data-open-iteration]').forEach(button=>button.onclick=async()=>{const sessionId=button.dataset.historySession||'';if(sessionId&&currentSession()?.session_id!==sessionId)await switchSession(sessionId);await openIteration(button.dataset.openIteration,button.dataset.candidateId||'',button.dataset.experimentId||'',true);});
  document.querySelectorAll('.validation-picker').forEach(picker=>{
    const inputs=[...picker.querySelectorAll('[data-validation-round]')];
    inputs.forEach((input,index)=>input.onchange=()=>{if(input.checked)inputs.slice(0,index).forEach(item=>item.checked=true);else inputs.slice(index+1).forEach(item=>item.checked=false);});
    const button=picker.querySelector('[data-prepare-selected-validation]');if(button)button.onclick=async()=>{const selected=inputs.filter(item=>item.checked);if(!selected.length){toast('请至少选择一轮未验证修改');return;}const target=selected[selected.length-1],included=(target.dataset.validationIncluded||'').split(',').filter(Boolean);await openIteration(target.dataset.validationRound,target.dataset.validationCandidate,target.dataset.validationExperiment||'',false,included);$('experimentPanel').classList.remove('hidden');$('experimentPanel').scrollIntoView({behavior:'smooth'});toast(`已选择 ${selected.length} 个未验证轮次；实验将验证截至第 ${selected.length} 个所选轮次的累计 Rubrics`);};
  });
}

function renderHistory(){
  const session=currentSession(),selectedCandidateId=state.candidate?.candidate_id||'';
  if(!session){
    const sessions=state.iterationHistory?.sessions||[];$('historyHint').textContent='全部 Session 按最近更新时间排列；展开一个 Session，再查看其中的 Rubrics 版本和迭代轮次。';$('historyEmpty').classList.toggle('hidden',!!sessions.length);
    $('historyGroups').innerHTML=sessions.map(item=>{const running=item.active_experiment_count?`<em>${esc(item.active_experiment_count)} 个验证实验运行中</em>`:'';return `<details class="history-session"><summary><span><b>${esc(item.session_id)}</b><small>Rubrics ${esc(item.rubric_version||'未知版本')} · ${esc(item.iteration_count)} 轮 · 最近 ${esc(timeLabel(item.updated_at))}</small></span>${running}</summary><div>${historyGroupsHtml(item.groups,item.session_id)}</div></details>`;}).join('');bindHistoryActions();return;
  }
  const groups=state.iterationHistory?.groups||[];$('historyHint').textContent=`当前查看：${session.session_id}。按父 Rubrics 版本归档，并区分已验证与尚未验证的反馈轮次。`;$('historyEmpty').classList.toggle('hidden',!!groups.length);
  $('historyGroups').innerHTML=historyGroupsHtml(groups,session.session_id,selectedCandidateId);bindHistoryActions();
}

async function ensureBatch(addCurrent=true){
  if(state.batch&&state.batch.status==='draft'){
    if((state.batch.memory_user||'local')!==selectedMemoryUser())throw new Error(`当前反馈轮次属于标注者 ${state.batch.memory_user||'local'}；请切回该用户，或完成本轮后再切换。`);
    if(state.batch.session_id!==state.report?.session_id||state.batch.rubric_sha256!==state.report?.rubric_sha256)throw new Error('当前报告与本轮反馈的 Session 或 Rubrics 版本不一致，请开始新一轮');
    if(addCurrent)state.batch=await api('/api/rubrics-loop/batches/add-report','POST',{session_id:state.batch.session_id,batch_id:state.batch.batch_id,report_ref:reportRef()});
    renderBatch();return state.batch;
  }
  if(!state.report)throw new Error('请先选择报告');
  state.batch=await api('/api/rubrics-loop/batches','POST',{session_id:state.report.session_id,report_ref:addCurrent?reportRef():null,memory_user:selectedMemoryUser()});clearCandidateView();renderBatch();return state.batch;
}

function renderBatch(){
  const batch=state.batch||{report_refs:[],feedback:[]},has=!!state.batch;
  const reportCount=has?(batch.report_refs||[]).length:0,feedbackCount=has?(batch.feedback||[]).length:0,editable=batch?.status==='draft';
  $('batchMeta').className=feedbackCount?'rubric-summary':'empty';$('batchMeta').innerHTML=feedbackCount?`<span>${reportCount} 篇报告</span><span>${feedbackCount} 条反馈</span><span>Rubrics ${esc(batch.rubric_version)}</span><span>标注者 ${esc(batch.memory_user||'local')}</span>`:'尚未添加反馈';
  const feedbackHtml=item=>{
    const editing=state.editingFeedbackId===item.feedback_id,label={inline:'原文批注',report:'整篇反馈',batch:'本轮共性意见'}[item.scope]||item.scope;
    const controls=editable?(editing?`<button data-cancel-edit-feedback="${esc(item.feedback_id)}">取消</button>`:`<button data-edit-feedback="${esc(item.feedback_id)}">编辑</button><button data-delete-feedback="${esc(item.feedback_id)}" class="danger">删除</button>`):'';
    return `<div class="feedback-item"><div class="feedback-item-head"><b>${esc(label)}</b><div class="feedback-controls">${controls}</div></div>${item.quote?`<small>“${esc(item.quote.slice(0,120))}”</small>`:''}${editing&&editable?`<textarea class="feedback-edit-input" data-feedback-edit-input="${esc(item.feedback_id)}">${esc(item.content)}</textarea><div class="feedback-edit-actions"><button class="primary" data-save-edit-feedback="${esc(item.feedback_id)}">保存修改</button></div>`:`<div>${esc(item.content)}</div>${item.updated_at?'<small>已编辑</small>':''}`}</div>`;
  };
  const reportSections=(batch.report_refs||[]).map(ref=>{
    const items=(batch.feedback||[]).filter(item=>item.scope!=='batch'&&item.report_ref?.skill_version===ref.skill_version&&item.report_ref?.case_id===ref.case_id);
    return `<details class="annotated-report"${items.some(item=>item.feedback_id===state.editingFeedbackId)?' open':''}><summary><span><b>${esc(caseLabel(currentSession()||{},ref.case_id))}</b><small>${esc(ref.skill_version)} · ${esc(ref.case_id)}</small></span><em>${items.length} 条意见</em></summary><div class="report-feedback-list">${items.map(feedbackHtml).join('')||'<div class="empty">暂无意见</div>'}</div></details>`;
  });
  const batchItems=(batch.feedback||[]).filter(item=>item.scope==='batch');
  if(batchItems.length)reportSections.push(`<details class="annotated-report"${batchItems.some(item=>item.feedback_id===state.editingFeedbackId)?' open':''}><summary><span><b>本轮共性意见</b><small>适用于本轮所有已批注报告</small></span><em>${batchItems.length} 条意见</em></summary><div class="report-feedback-list">${batchItems.map(feedbackHtml).join('')}</div></details>`);
  $('batchReports').className='annotated-reports'+(reportSections.length?'':' empty');$('batchReports').innerHTML=reportSections.join('')||'添加反馈后会自动关联报告';
  document.querySelectorAll('[data-delete-feedback]').forEach(button=>button.onclick=()=>deleteFeedback(button.dataset.deleteFeedback));
  document.querySelectorAll('[data-edit-feedback]').forEach(button=>button.onclick=()=>{state.editingFeedbackId=button.dataset.editFeedback;renderBatch();document.querySelector(`[data-feedback-edit-input="${CSS.escape(state.editingFeedbackId)}"]`)?.focus();});
  document.querySelectorAll('[data-cancel-edit-feedback]').forEach(button=>button.onclick=()=>{state.editingFeedbackId='';renderBatch();});
  document.querySelectorAll('[data-save-edit-feedback]').forEach(button=>button.onclick=()=>updateFeedback(button.dataset.saveEditFeedback,document.querySelector(`[data-feedback-edit-input="${CSS.escape(button.dataset.saveEditFeedback)}"]`)?.value||''));
  renderRoutingReview(batch);
  $('feedbackActions').classList.toggle('hidden',!feedbackCount||!editable);$('generateCandidate').disabled=!feedbackCount||!editable;
  const routing=batch.routing||{},routingReviewable=['review','memory_failed'].includes(routing.status);$('generateCandidate').textContent=routingReviewable?(routing.status==='memory_failed'?'重试 Feedback 处理':'确认分类并同时处理'):routing.status==='completed'?'继续处理 Rubrics Feedback':'提交已有批注';
  const allOption=$('feedbackScope').querySelector('option[value="all"]');allOption.textContent=`本次所有已批注报告${reportCount?`（${reportCount} 篇）`:''}`;
}

function renderRoutingReview(batch){
  const panel=$('routingReview'),routing=batch?.routing||{},routes=routing.routes||[],feedbackById=new Map((batch?.feedback||[]).map(item=>[item.feedback_id,item]));
  if(state.classificationRunning){
    panel.classList.remove('hidden');
    panel.innerHTML='<div class="classification-running"><span class="optimizer-spinner" aria-hidden="true"><i></i><i></i><i></i></span><div><b>正在对feedback进行分类</b><small>判断每条反馈属于通用 Rubrics、个性化 Memory 或仅记录</small></div></div>';
    return;
  }
  panel.classList.toggle('hidden',!routes.length);if(!routes.length){panel.innerHTML='';return;}
  const labels={rubric:'通用 Rubrics',memory:'个性化 Memory',ignore:'仅记录'},statusLabels={review:'待确认',processing:'处理中',memory_failed:'Memory 处理失败',completed:'已完成'},resultLabel=result=>result.status==='pending'?'已存为 L0 待观察':result.status==='error'?'处理失败':result.status==='ignored'?'Memory Agent 决定不写入':result.status==='unchanged'?'Memory 已存在，无需重复写入':`已更新 Memory${result.profiles_written?` · L2 ×${result.profiles_written}`:''}`;
  panel.innerHTML=`<div class="routing-head"><div><b>Feedback 分类结果</b><small>${['review','memory_failed'].includes(routing.status)?'确认去向；归类为 Memory 时，再选择保存层级':'分类已确认并处理'}</small></div><span>${esc(statusLabels[routing.status]||routing.status||'')}</span></div>`+routes.map(route=>{const feedback=feedbackById.get(route.feedback_id)||{},result=route.memory_result||{},locked=!['review','memory_failed'].includes(routing.status),memoryAction=route.memory_action||'pending';return `<div class="routing-item"><div><b>${esc(feedback.content||route.feedback_id)}</b><small>${esc(route.reason||'')}</small></div><div class="routing-controls"><select data-route-feedback="${esc(route.feedback_id)}"${locked?' disabled':''}>${['rubric','memory','ignore'].map(value=>`<option value="${value}"${route.destination===value?' selected':''}>${labels[value]}</option>`).join('')}</select>${route.destination==='memory'?`<select data-memory-action="${esc(route.feedback_id)}"${locked?' disabled':''}><option value="pending"${memoryAction==='pending'?' selected':''}>仅记录，待观察（L0）</option><option value="store"${memoryAction==='store'?' selected':''}>保存为稳定偏好（L1 + L2）</option></select>`:''}${route.destination==='memory'&&result.status?`<em>${esc(resultLabel(result))} · ${esc(result.memory_user||batch.memory_user||'local')}</em>`:''}</div></div>`;}).join('');
  document.querySelectorAll('[data-route-feedback]').forEach(select=>select.onchange=()=>{const route=routes.find(item=>item.feedback_id===select.dataset.routeFeedback);if(route)route.destination=select.value;renderRoutingReview(batch);});
}

async function refreshHistory(){const session=currentSession();if(!session)return;state.iterationHistory=await api('/api/rubrics-loop/iterations?session_id='+encodeURIComponent(session.session_id));state.draft=state.iterationHistory.active_draft||null;renderHistory();}

async function addFeedback(scope,content,quote='',renderedQuote=''){
  if(!content.trim()){toast('请先输入意见');return false;}
  try{await ensureBatch(true);state.batch=await api('/api/rubrics-loop/feedback','POST',{session_id:state.batch.session_id,batch_id:state.batch.batch_id,scope,content,report_ref:scope==='batch'?null:reportRef(),quote,rendered_quote:renderedQuote});renderBatch();await refreshHistory();toast('意见已保存到本轮反馈');return true;}catch(error){toast(error.message);return false;}
}
async function deleteFeedback(id){try{state.batch=await api('/api/rubrics-loop/feedback','POST',{action:'delete',session_id:state.batch.session_id,batch_id:state.batch.batch_id,feedback_id:id});if(state.editingFeedbackId===id)state.editingFeedbackId='';renderBatch();await refreshHistory();}catch(error){toast(error.message);}}
async function updateFeedback(id,content){if(!content.trim()){toast('意见不能为空');return;}try{state.batch=await api('/api/rubrics-loop/feedback','POST',{action:'update',session_id:state.batch.session_id,batch_id:state.batch.batch_id,feedback_id:id,content});state.editingFeedbackId='';renderBatch();await refreshHistory();toast('意见已更新');}catch(error){toast(error.message);}}

function setupModelSelectors(){
  const backends=state.config.llm_backends||['api','codex','workbuddy'];['optimizerBackend','skillBackend','judgeBackend','acceptanceBackend'].forEach(id=>{$(id).innerHTML=backends.map(value=>`<option value="${value}">${value==='workbuddy'?'WB CLI':value==='codex'?'Codex CLI':'API'}</option>`).join('');});
  const runnerModels=state.config.models||state.config.evaluation_models||[],runnerDefault=state.config.model||runnerModels[0]||'';$('runnerModel').innerHTML=runnerModels.map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');if(runnerDefault&&runnerModels.includes(runnerDefault))$('runnerModel').value=runnerDefault;
  $('optimizerBackend').value=state.config.optimizer_llm_backend||'api';$('skillBackend').value=state.config.optimizer_llm_backend||'api';$('judgeBackend').value=state.config.judge_llm_backend||'api';$('acceptanceBackend').value=state.config.judge_llm_backend||'api';
  ['optimizer','skill','judge','acceptance'].forEach(prefix=>{const backend=$(prefix+'Backend');backend.onchange=()=>renderModelSelect(prefix);renderModelSelect(prefix);});
  ['optimizerEffort','skillEffort','judgeEffort','acceptanceEffort'].forEach(id=>{$(id).innerHTML=(state.config.codex_reasoning_efforts||[]).map(value=>`<option>${esc(value)}</option>`).join('');});
}
function renderModelSelect(prefix){const backend=$(prefix+'Backend').value,select=$(prefix+'Model'),judgeLike=['judge','acceptance'].includes(prefix);let models,def;if(backend==='api'){models=state.config.api_models;def=judgeLike?state.config.judge_api_model:state.config.optimizer_api_model;}else if(backend==='codex'){models=state.config.codex_models;def=judgeLike?state.config.judge_codex_model:state.config.optimizer_codex_model;}else{models=state.config.evaluation_models;def=judgeLike?state.config.judge_wb_model:state.config.optimizer_wb_model;}select.innerHTML=(models||[]).map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('');if(def&&[...select.options].some(option=>option.value===def))select.value=def;const effortLabel=$(prefix==='optimizer'?'effortLabel':prefix+'EffortLabel');if(effortLabel)effortLabel.classList.toggle('hidden',backend!=='codex');}
function modelPayload(prefix){return {llm_backend:$(prefix+'Backend').value,llm_model:$(prefix+'Model').value,llm_reasoning_effort:$(prefix+'Backend').value==='codex'?$(prefix+'Effort').value:null};}
function backendLabel(value){return value==='workbuddy'?'WB CLI':value==='codex'?'Codex CLI':'API';}
function showOptimizerStatus(payload){$('candidatePanel').classList.remove('hidden');$('optimizerRunDetail').textContent=`预计等待 1–3 分钟。调用方式：${backendLabel(payload.llm_backend)}；模型：${payload.llm_model||'默认模型'}`;$('optimizerRunStatus').classList.remove('hidden');$('candidatePanel').scrollIntoView({behavior:'smooth',block:'start'});}
function confirmRedlineChanges(candidate){
  const changes=candidate?.validation?.redline_changes||[];
  if(!changes.length)return true;
  const candidateRedlines=new Set(rubricCheckRows(candidate.candidate_rubric).filter(item=>item.check.redline).map(item=>item.check.id));
  const enabled=changes.filter(id=>candidateRedlines.has(id)),disabled=changes.filter(id=>!candidateRedlines.has(id)),details=[];
  if(enabled.length)details.push(`设为红线：${enabled.join('、')}`);
  if(disabled.length)details.push(`取消红线：${disabled.join('、')}`);
  return confirm(`检测到候选 Rubrics 修改了红线属性：\n${details.join('\n')}\n\n红线会影响维度封顶和最终得分，是否继续验证？`);
}

function rubricCheckRows(rubric){return (rubric?.dimensions||[]).flatMap(dimension=>(dimension.checks||[]).map(check=>({dimension:dimension.name_zh||dimension.name||'',check})));}
function checkPublicValue(check){if(!check)return null;return {label:check.label||'',desc:check.desc||'',effect:check.effect||'',redline:!!check.redline};}
function checkBody(check){if(!check)return '<span class="deleted-check">已删除</span>';return `<b>${esc(check.label||'')}</b><p>${esc(check.desc||'')}</p>${check.effect?`<small>判定影响：${esc(check.effect)}</small>`:''}${check.redline?'<em class="redline-mark">红线</em>':''}`;}
function candidateDiff(parent,candidate){
  const before=new Map(rubricCheckRows(parent).map(item=>[item.check.id,item])),after=new Map(rubricCheckRows(candidate).map(item=>[item.check.id,item]));
  return [...new Set([...before.keys(),...after.keys()])].map(id=>{const oldItem=before.get(id),newItem=after.get(id);let type='unchanged';if(!oldItem)type='added';else if(!newItem)type='deleted';else if(JSON.stringify(checkPublicValue(oldItem.check))!==JSON.stringify(checkPublicValue(newItem.check)))type='modified';return {id,type,before:oldItem,after:newItem};});
}
function analysisDecision(item,operationFeedbackIds=new Set()){
  const modifying=operationFeedbackIds.has(item.feedback_id)||['add_check','update_check','merge_checks','delete_check','move_check','update_existing_check','update_existing_checks'].includes(item.decision),category=item.category||'';
  if(modifying)return {label:'需要修改 · 已纳入候选 Rubrics',className:'changed'};
  if(category==='data')return {label:'属于任务配置 · 不修改通用 Rubrics',className:'scoped'};
  if(category==='one_off_preference')return {label:'单次偏好 · 不修改通用 Rubrics',className:'scoped'};
  return {label:'现有 Rubrics 已覆盖 · 无需修改',className:'covered'};
}
function renderCandidateAnalysis(candidate){
  const feedbackById=new Map((state.batch?.feedback||[]).map(item=>[item.feedback_id,item]));
  const operationFeedbackIds=new Set((candidate.operations||[]).flatMap(operation=>operation.feedback_ids||[]));
  $('analysisList').innerHTML=(candidate.feedback_analysis||[]).map(item=>{const result=analysisDecision(item,operationFeedbackIds),feedback=feedbackById.get(item.feedback_id),checks=(item.existing_check_ids||[]).join('、')||'未对应具体 Check',hasQuote=feedback?.scope==='inline'&&feedback?.quote;return `<div class="analysis-item ${result.className}${hasQuote?' has-quote':''}"${hasQuote?' tabindex="0"':''}><div class="analysis-head"><b>${esc(feedback?.content||item.feedback_id||'反馈')}</b><span>${esc(result.label)}</span></div><small>对应 Checks：${esc(checks)}</small><p>${esc(item.reason||'')}</p>${hasQuote?`<span class="analysis-quote-hint">悬浮查看原文批注</span><span class="history-quote-tooltip analysis-quote-tooltip" role="tooltip"><strong>当时选中的 Markdown 原文</strong><span>${esc(feedback.quote)}</span></span>`:''}</div>`;}).join('')||'<div class="empty">Optimizer 未返回反馈处理明细</div>';
}
function renderCandidateChanges(diffs){
  const changed=diffs.filter(item=>item.type!=='unchanged');
  $('operationList').innerHTML=changed.map(item=>`<article class="diff-card ${esc(item.type)}"><header><b>${esc(item.id)}</b><span>${esc({modified:'修改',added:'新增',deleted:'删除'}[item.type])}</span></header><div class="diff-sides"><section><small>修改前</small><div>${checkBody(item.before?.check)}</div></section><section><small>修改后</small><div>${checkBody(item.after?.check)}</div></section></div></article>`).join('')||'<div class="no-change">本轮没有修改 Rubrics；所有反馈均由现有 Checks 覆盖或被判定为非通用问题。</div>';
}
function renderCandidateTable(candidate,diffs){
  const changedIds=new Set(diffs.filter(item=>item.type!=='unchanged').map(item=>item.id));
  const rows=rubricCheckRows(candidate).map(item=>`<tr class="${changedIds.has(item.check.id)?'changed-row':''}"><td>${esc(item.dimension)}</td><td><b>${esc(item.check.id)}</b>${changedIds.has(item.check.id)?'<span class="changed-badge">本轮修改</span>':''}</td><td>${checkBody(item.check)}</td></tr>`).join('');
  $('candidateRubricTable').innerHTML=`<div class="candidate-table-wrap"><table class="candidate-rubric-table"><thead><tr><th>维度</th><th>Check ID</th><th>Check 内容</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function selectedCandidateContext(candidateId){
  for(const group of state.iterationHistory?.groups||[]){
    const iterations=group.iterations||[];
    const chronological=iterations.slice().sort((left,right)=>(left.created_at||0)-(right.created_at||0));
    for(let index=0;index<chronological.length;index+=1){
      const iteration=chronological[index];
      if((iteration.candidates||[]).some(candidate=>candidate.candidate_id===candidateId))return {round:index+1,rubricVersion:group.rubric_version};
    }
  }
  return null;
}

async function generateCandidate(revise=false){
  if(!state.batch)return;const payload={session_id:state.batch.session_id,batch_id:state.batch.batch_id,...modelPayload('optimizer')};if(revise){payload.candidate_id=state.candidate.candidate_id;payload.revision_note=$('revisionNote').value.trim();}
  try{
    $('generateCandidate').disabled=true;$('reviseCandidate').disabled=true;
    if(!revise&&state.batch.routing?.status!=='completed'){
      if(!['review','memory_failed'].includes(state.batch.routing?.status)){
        state.classificationRunning=true;$('status').textContent='正在对feedback进行分类…';renderRoutingReview(state.batch);
        state.batch=await api('/api/rubrics-loop/feedback/route','POST',payload);state.classificationRunning=false;renderBatch();await refreshHistory();toast('分类完成，请确认每条 Feedback 的去向');return;
      }
      const destinations={},memoryActions={};document.querySelectorAll('[data-route-feedback]').forEach(select=>destinations[select.dataset.routeFeedback]=select.value);document.querySelectorAll('[data-memory-action]').forEach(select=>memoryActions[select.dataset.memoryAction]=select.value);
      const rubricCount=Object.values(destinations).filter(value=>value==='rubric').length;
      $('status').textContent=rubricCount?'Memory 与 Rubrics Optimizer 并行处理中…':'正在保存 Memory…';if(rubricCount)showOptimizerStatus(payload);
      const result=await api('/api/rubrics-loop/feedback/confirm-and-optimize','POST',{...payload,destinations,memory_actions:memoryActions});state.batch=result.batch;state.candidate=result.candidate||null;renderBatch();await Promise.all([refreshHistory(),refreshMemory()]);
      if(!state.candidate){toast('本轮已完成，Memory 已保存，无需生成候选 Rubrics');return;}
      renderCandidate();toast(state.candidate.validation.ok?'Memory 已保存，候选 Rubrics 已生成':'Memory 已保存，候选 Rubrics 未通过静态校验');return;
    }
    $('status').textContent='Rubrics Optimizer 运行中…';showOptimizerStatus(payload);state.candidate=await api(revise?'/api/rubrics-loop/candidates/revise':'/api/rubrics-loop/candidates','POST',payload);await refreshHistory();renderCandidate();toast(state.candidate.validation.ok?'候选 Rubrics 已生成':'候选 Rubrics 未通过静态校验');
  }catch(error){state.classificationRunning=false;renderRoutingReview(state.batch);toast(error.message);}finally{const noRubric=state.batch?.routing?.status==='completed'&&!(state.batch.routing.routes||[]).some(item=>item.destination==='rubric');$('optimizerRunStatus').classList.add('hidden');$('status').textContent='就绪';$('generateCandidate').disabled=!!state.candidate||noRubric;$('reviseCandidate').disabled=false;}}
function renderCandidate(scroll=true){
  const c=state.candidate;if(!c)return;
  $('candidatePanel').classList.remove('hidden');$('candidateEmpty').classList.add('hidden');$('candidateView').classList.remove('hidden');$('feedbackActions').classList.add('hidden');$('saveFeedback').disabled=true;
  const context=selectedCandidateContext(c.candidate_id);$('candidateTitle').textContent=context?`第 ${context.round} 轮候选 Rubrics 审核`:'候选 Rubrics 审核';$('candidateContext').textContent=context?`Rubrics ${context.rubricVersion} · ${statusLabel(c.status)}。确认反馈处理和具体变化后，再决定暂存、验证或拒绝。`:'先确认反馈处理和 Rubrics 变化，再进入验证实验。';
  const v=c.validation||{},reviewable=['draft','validated','awaiting_review'].includes(c.status),editable=['draft','validated'].includes(c.status),staged=c.status==='staged';
  const workingParent=c.working_parent_rubric||state.batch?.working_rubric||state.batch?.parent_rubric;
  const diffs=candidateDiff(workingParent,c.candidate_rubric);
  const revisionCount=c.draft_revision_count||state.draft?.revision_count||0;
  $('draftBanner').classList.toggle('hidden',!c.draft_id&&!state.draft);
  $('draftBanner').innerHTML=(c.draft_id||state.draft)?`<b>待验证 Rubrics 草案 · 已累计 ${esc(revisionCount)} 轮</b><span>后续 Feedback 会在当前草案上继续修改；最终验证仍以 Session 冻结 Rubric 为基线检查总长度。</span>`:'';
  $('candidateSummary').innerHTML=[[statusLabel(c.status),'状态'],[`${v.candidate_check_count}/${v.parent_check_count}`,'累计 Checks'],[`${diffs.filter(item=>item.type!=='unchanged').length}`,'本轮变更'],[v.repeated_check_ids?.length?v.repeated_check_ids.join(', '):'无','重复修改 Check']].map(item=>`<div><small>${esc(item[1])}</small><b>${esc(item[0])}</b></div>`).join('');
  renderCandidateAnalysis(c);renderCandidateChanges(diffs);renderCandidateTable(c.candidate_rubric,diffs);
  $('candidateJson').value=JSON.stringify(c.candidate_rubric,null,2);$('candidateJson').disabled=!editable;$('saveCandidateJson').disabled=!editable;
  $('revisionNote').disabled=!reviewable;$('reviseCandidate').disabled=!reviewable;$('rejectCandidate').disabled=!reviewable;
  $('stageCandidate').disabled=!v.ok||!['validated','awaiting_review'].includes(c.status);$('stageCandidate').classList.toggle('hidden',staged||['running','adopted','rejected'].includes(c.status));
  const existingExperiment=state.experiment?.candidate_id===c.candidate_id&&state.experiment?.experiment_session_id;
  const mustStageFirst=!!c.draft_id&&!staged&&c.status!=='awaiting_review';$('prepareExperiment').disabled=existingExperiment?false:(!v.ok||mustStageFirst||!['validated','staged','awaiting_review'].includes(c.status));$('prepareExperiment').textContent=existingExperiment?'查看验证实验详情':'验证新 Rubrics';
  if(scroll)$('candidatePanel').scrollIntoView({behavior:'smooth',block:'start'});
}

async function switchSession(sessionId=''){$('sessionSelect').value=sessionId;state.report=null;state.batch=null;state.candidate=null;state.experiment=null;state.draft=null;state.iterationHistory=null;state.validationSelection=null;state.editingFeedbackId='';$('currentReportTools').classList.add('hidden');$('judgeScore').classList.add('hidden');$('reportTitle').textContent='请选择报告';$('reportMeta').innerHTML='';$('report').innerHTML='';$('reportHint').textContent=sessionId?'从上方展开一个 Skill 版本，再选择报告。':'选择 Session 后查看报告，或直接浏览下方全部迭代历史。';renderSession();renderBatch();clearCandidateView();await restoreWorkflow();}
$('sessionSelect').onchange=()=>switchSession($('sessionSelect').value);
$('refresh').onclick=()=>loadContext($('sessionSelect').value).catch(error=>toast(error.message));
$('report').onmouseup=async()=>{const selection=window.getSelection();const text=selection&&String(selection).trim();if(!text||!state.report?.annotatable||!$('report').contains(selection.anchorNode))return;const requestId=++state.selectionRequestId;state.selectedQuote='';state.selectedRenderedQuote=text;$('selectedQuote').textContent='正在还原 Markdown 原文…';$('selectionPreview').classList.remove('hidden');$('feedbackScope').value='current';$('feedbackScope').disabled=true;$('feedbackModeLabel').textContent='针对选中原文的反馈';$('saveFeedback').disabled=true;try{const result=await api('/api/rubrics-loop/feedback/resolve-selection','POST',{session_id:state.report.session_id,report_ref:reportRef(),rendered_text:text});if(requestId!==state.selectionRequestId)return;state.selectedQuote=result.markdown_quote;state.selectedRenderedQuote=result.rendered_quote;$('selectedQuote').textContent=result.markdown_quote;$('saveFeedback').disabled=false;$('feedbackInput').focus();}catch(error){if(requestId!==state.selectionRequestId)return;clearSelection();toast(error.message);}};
$('cancelSelection').onclick=()=>{window.getSelection()?.removeAllRanges();clearSelection();};
$('saveFeedback').onclick=async()=>{const input=$('feedbackInput'),scope=$('feedbackScope').value==='all'?'batch':(state.selectedQuote?'inline':'report');if(await addFeedback(scope,input.value.trim(),state.selectedQuote,state.selectedRenderedQuote)){input.value='';window.getSelection()?.removeAllRanges();clearSelection();}};
$('feedbackInput').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('saveFeedback').disabled)$('saveFeedback').click();}};
$('continueAnnotating').onclick=()=>{clearSelection();$('feedbackInput').value='';const selector=document.querySelector('.selector');selector.scrollIntoView({behavior:'smooth',block:'start'});const closed=document.querySelector('#versionTree details:not([open]) summary');if(closed)closed.focus();toast('从报告列表选择下一篇报告，已有反馈会继续保留');};
$('closeCandidateReview').onclick=()=>{clearCandidateView();$('historyGroups').scrollIntoView({behavior:'smooth',block:'start'});};
$('generateCandidate').onclick=()=>generateCandidate(false);$('reviseCandidate').onclick=()=>generateCandidate(true);
$('refreshMemory').onclick=()=>refreshMemory();
$('memoryUser').onchange=()=>{refreshMemory();if(state.batch?.status==='draft'&&(state.batch.feedback||[]).length)toast(`当前草稿仍属于标注者 ${state.batch.memory_user||'local'}`);};
$('saveCandidateJson').onclick=async()=>{try{const candidateRubric=JSON.parse($('candidateJson').value);state.candidate=await api('/api/rubrics-loop/candidates/edit','POST',{session_id:state.candidate.session_id,candidate_id:state.candidate.candidate_id,candidate_rubric:candidateRubric});renderCandidate();await refreshHistory();toast(state.candidate.validation.ok?'手工修改已保存':'已保存，但未通过静态校验');}catch(error){toast('保存失败：'+error.message);}};
$('rejectCandidate').onclick=async()=>{const reason=$('revisionNote').value.trim()||prompt('请填写拒绝原因')||'';try{state.candidate=await api('/api/rubrics-loop/candidates/reject','POST',{session_id:state.candidate.session_id,candidate_id:state.candidate.candidate_id,reason});renderCandidate();await refreshHistory();toast('候选 Rubrics 已拒绝');}catch(error){toast(error.message);}};
$('stageCandidate').onclick=async()=>{try{const needsConflict=!!state.candidate?.validation?.requires_history_conflict_confirmation;const confirmed=!needsConflict||confirm('本轮会替换或冲突处理此前对同一 Check 的修改。确认按当前候选暂存吗？');if(!confirmed)return;const result=await api('/api/rubrics-loop/candidates/stage','POST',{session_id:state.candidate.session_id,candidate_id:state.candidate.candidate_id,history_conflict_confirmed:confirmed});state.candidate=result.candidate;state.draft={draft_id:result.draft.draft_id,status:result.draft.status,revision_count:(result.draft.revisions||[]).length,touched_check_ids:[...new Set((result.draft.revisions||[]).flatMap(item=>item.touched_check_ids||[]))]};renderCandidate(false);await refreshHistory();toast(`已暂存到待验证草案，共累计 ${state.draft?.revision_count||1} 轮修改`);}catch(error){toast(error.message);}};
$('prepareExperiment').onclick=()=>{const existing=state.experiment?.candidate_id===state.candidate?.candidate_id;if(existing)renderExperiment();$('experimentPanel').classList.remove('hidden');$('experimentPanel').scrollIntoView({behavior:'smooth'});};
function acceptanceStatusLabel(value){return {followed:'已遵循',partially_followed:'部分遵循',not_followed:'未遵循',unable_to_judge:'无法判断'}[value]||value||'待验收';}
function stabilityLabel(value){return {stable:'连续两轮稳定遵循',improving:'逐轮改善',unstable:'两轮表现不稳定',not_improved:'未改善',unknown:'稳定性未知'}[value]||value||'';}
function failureLayerLabel(value){return {none:'无失败',rubric_gap:'Rubrics 缺口',rubric_not_operational:'Rubrics 不可执行',skill_translation_failure:'Skill 转译失败',runner_execution_failure:'Runner 执行失败',data_issue:'数据问题',one_off_feedback:'一次性偏好',feedback_conflict:'Feedback 冲突',unknown:'原因未知'}[value]||value||'原因未知';}
function skillLoopProgress(result,rounds){
  const live=result.live_state||{},versions=live.versions||[],settled=versions.filter(item=>['adopted','rejected'].includes(item.candidate_state)).length,finalCurve=result.final_state?.curve||[],completed=Math.min(rounds,live.current_version?settled:Math.max(0,finalCurve.length-1));
  const running=result.status==='running'&&result.phase==='skill_loop',current=Math.min(rounds,completed+(running&&completed<rounds?1:0));
  if(!running)return {completed,current,detail:completed>=rounds?`已完成 ${completed} 轮`:''};
  const version=live.current_version||'',progress=live.judge_progress||{},total=Number(progress.total_cases||0),reports=Number(progress.reports_ready||0),judged=Number(progress.judged_cases||0);let stage='正在准备本轮';
  if((progress.missing_report_case_ids||[]).length)stage=`报告生成 ${reports}/${total}`;
  else if(progress.required&&!progress.complete)stage=`Judge ${judged}/${total}`;
  else if(live.actions?.advance?.enabled)stage='Skill Optimizer 生成下一版';
  else if(live.pending_candidate)stage='候选 Skill 处理中';
  return {completed,current,detail:`已完成 ${completed} 轮 · 第 ${current} 轮${version?` · ${version}`:''} · ${stage}`};
}
function renderExperiment(){
  const result=state.experiment;if(!result)return;
  $('memoryEnabled').checked=result.config?.memory_enabled===true;
  const phase=result.phase||'skill_loop',rounds=Number(result.config?.skill_iteration_rounds||2),skillProgress=skillLoopProgress(result,rounds),acceptance=result.acceptance||{};
  const steps=[{key:'skill_loop',label:`Skill 迭代 ${skillProgress.current}/${rounds}`,detail:skillProgress.detail},{key:'feedback_acceptance',label:'Rubrics 验收',detail:phase==='feedback_acceptance'?'正在逐条核验反馈落实情况':''},{key:'decision',label:'人工决策',detail:''}],phaseIndex={skill_loop:0,feedback_acceptance:1,decision:2}[phase]??0;
  $('experimentProgress').innerHTML=steps.map((step,index)=>{const done=index<phaseIndex||result.status==='completed',active=index===phaseIndex&&!done,running=active&&['queued','running'].includes(result.status);return `<div class="experiment-step ${done?'done':active?'active':''}"><span>${done?'✓':index+1}</span><div><b>${esc(step.label)}</b>${step.detail?`<small>${esc(step.detail)}</small>`:''}</div>${running?'<div class="experiment-running-dots" aria-label="运行中"><i></i><i></i><i></i></div>':''}</div>`;}).join('');
  $('experimentResult').textContent=JSON.stringify({status:result.status,phase:result.phase,experiment_session_id:result.experiment_session_id,live_state:result.live_state,final_state:result.final_state,acceptance:result.acceptance,comparison:result.comparison,error:result.error},null,2);
  if(result.experiment_session_id){$('openExperiment').href='/?id='+encodeURIComponent(result.experiment_session_id);$('openExperiment').classList.remove('hidden');}else $('openExperiment').classList.add('hidden');
  const acceptanceItems=acceptance.feedback_results||[];$('acceptancePanel').classList.toggle('hidden',!acceptanceItems.length&&acceptance.status!=='running');
  const model=acceptance.model_config||result.config?.acceptance||{};$('acceptanceSummary').textContent=acceptance.status==='running'?`运行中… ${backendLabel(model.llm_backend)} · ${model.llm_model||'默认模型'}`:`${acceptanceStatusLabel(acceptance.overall_status)} · ${acceptanceItems.length} 条 Feedback`;
  $('acceptanceResults').innerHTML=acceptanceItems.map((item,index)=>`<details class="acceptance-item ${esc(item.status)}"${item.status!=='followed'?' open':''}><summary><span><b>Feedback ${index+1} · ${esc(acceptanceStatusLabel(item.status))}</b><small>${esc(stabilityLabel(item.stability))} · ${esc(failureLayerLabel(item.failure_layer))}</small></span></summary><div>${item.feedback_content?`<div class="acceptance-feedback"><b>原始 Feedback</b><p>${esc(item.feedback_content)}</p>${item.feedback_quote?`<details><summary>查看原文批注</summary><pre>${esc(item.feedback_quote)}</pre></details>`:''}</div>`:''}<p>${esc(item.reason||'')}</p>${(item.evidence||[]).map(evidence=>`<blockquote><small>${esc(evidence.phase)} · ${esc(evidence.skill_version)} · ${esc(evidence.case_id)}</small>${esc(evidence.quote)}${evidence.assessment?`<em>${esc(evidence.assessment)}</em>`:''}</blockquote>`).join('')||'<div class="empty">没有可核验的原文证据</div>'}${item.next_action?`<p><b>下一步：</b>${esc(item.next_action)}</p>`:''}${(item.rubric_suggestions||[]).length?`<ul>${item.rubric_suggestions.map(value=>`<li>${esc(value)}</li>`).join('')}</ul>`:''}</div></details>`).join('');
  $('experimentError').classList.toggle('hidden',!result.error);$('experimentError').textContent=result.error?`运行失败：${result.error}`:'';
  const candidateStatus=state.candidate?.status,canDecide=result.status==='completed'&&['validated','awaiting_review','adopted'].includes(candidateStatus);$('experimentDecision').classList.toggle('hidden',!canDecide);$('adoptCandidate').classList.toggle('hidden',candidateStatus==='adopted');$('continueOptimization').classList.toggle('hidden',candidateStatus==='adopted');$('keepOriginalRubric').classList.toggle('hidden',candidateStatus==='adopted');
  $('startExperiment').textContent=result.status==='failed'?(result.loop_completed_at?'仅重试 Rubrics 验收':'从断点重试验证实验'):'启动 Skill 迭代与 Rubrics 验收';$('startExperiment').disabled=!['created','failed'].includes(result.status);
}
async function pollExperiment(){if(!state.experiment)return;try{const previous=state.experiment.status;const fresh=await api('/api/rubrics-loop/experiment?'+new URLSearchParams({session_id:state.candidate.session_id,experiment_id:state.experiment.experiment_id}));if(fresh.experiment_session_id&&fresh.status==='running'&&fresh.phase==='skill_loop'){try{fresh.live_state=await api('/api/session?id='+encodeURIComponent(fresh.experiment_session_id));}catch(error){fresh.live_state=state.experiment.live_state||null;}}state.experiment=fresh;if(!['queued','running'].includes(state.experiment.status)){state.candidate=await api('/api/rubrics-loop/candidate?'+new URLSearchParams({session_id:state.candidate.session_id,candidate_id:state.candidate.candidate_id}));renderCandidate(false);}renderExperiment();if(state.experiment.status!==previous)await refreshHistory();if(['queued','running'].includes(state.experiment.status))setTimeout(pollExperiment,2500);}catch(error){toast(error.message);}}
$('startExperiment').onclick=async()=>{const redlineConfirmed=confirmRedlineChanges(state.candidate);if(!redlineConfirmed)return;try{const retrying=state.experiment?.status==='failed';const config={runner_model:$('runnerModel').value.trim(),generation_parallel:+$('generationParallel').value,judge_parallel:+$('judgeParallel').value,skill_iteration_rounds:+$('skillIterationRounds').value||2,memory_enabled:$('memoryEnabled').checked,memory_user:state.batch?.memory_user||selectedMemoryUser(),feedback_acceptance_enabled:true,skill_optimizer:modelPayload('skill'),judge:modelPayload('judge'),acceptance:modelPayload('acceptance')};state.experiment=await api('/api/rubrics-loop/experiments','POST',{session_id:state.candidate.session_id,candidate_id:state.candidate.candidate_id,experiment_id:retrying?state.experiment.experiment_id:null,selected_batch_ids:retrying?null:state.validationSelection,config,redline_confirmed:!!state.candidate?.validation?.requires_redline_confirmation});state.candidate.status='running';renderCandidate(false);renderExperiment();await refreshHistory();pollExperiment();toast(retrying?(state.experiment.loop_completed_at?'将只补跑 Rubrics 验收':'已从原验证 Session 的断点续跑'):'验证实验已创建：默认迭代 2 轮后自动验收');}catch(error){toast(error.message);}};
$('adoptCandidate').onclick=async()=>{if(!confirm('确认将候选 Rubrics 采纳为新的不可变版本？'))return;try{const result=await api('/api/rubrics-loop/candidates/adopt','POST',{session_id:state.candidate.session_id,candidate_id:state.candidate.candidate_id});state.adopted=result;state.candidate.status='adopted';state.candidate.adopted_version=result.version;renderCandidate(false);renderExperiment();await refreshHistory();toast('已采纳 '+result.version);$('adoptCandidate').disabled=true;$('setDefaultRubric').classList.remove('hidden');$('experimentResult').textContent+='\n\n已采纳版本: '+result.version;}catch(error){toast(error.message);}};
$('continueOptimization').onclick=()=>{const suggestions=(state.experiment?.acceptance?.feedback_results||[]).flatMap(item=>item.rubric_suggestions||[]);$('revisionNote').value=suggestions.length?`Rubrics 验收未完全通过，请结合以下结果继续优化：\n- ${suggestions.join('\n- ')}`:'Rubrics 验收未完全通过，请结合验收证据继续优化。';$('candidatePanel').scrollIntoView({behavior:'smooth',block:'start'});$('revisionNote').focus();toast('已把 Rubrics 验收建议带入重新生成说明');};
$('keepOriginalRubric').onclick=async()=>{if(!confirm('确认保留原 Rubrics，并结束本轮候选验证？'))return;try{state.candidate=await api('/api/rubrics-loop/candidates/reject','POST',{session_id:state.candidate.session_id,candidate_id:state.candidate.candidate_id,reason:'Rubrics 验收后决定保留原 Rubrics'});renderCandidate(false);renderExperiment();await refreshHistory();toast('已保留原 Rubrics，本轮候选已结束');}catch(error){toast(error.message);}};
$('setDefaultRubric').onclick=async()=>{if(!state.adopted)return;if(!confirm('确认将 '+state.adopted.version+' 设为 Rubrics Loop 的默认版本？这不会改写仓库默认 Rubric 文件。'))return;try{await api('/api/rubrics-loop/rubrics/set-default','POST',{product_id:state.adopted.rubric.product,version:state.adopted.version});toast('已设为 Rubrics Loop 默认版本');$('setDefaultRubric').disabled=true;}catch(error){toast(error.message);}};

loadContext().catch(error=>{$('status').textContent='加载失败';toast(error.message);});
