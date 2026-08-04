(function(){
  let SB=null,byId=new Map();
  const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const inline=value=>escapeHTML(value).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2" target="_blank">$1</a>');
  function markdown(value){
    const lines=String(value||'').replace(/\r/g,'').split('\n'),out=[];let index=0,list=null;
    const closeList=()=>{if(list){out.push('</'+list+'>');list=null}};
    while(index<lines.length){let line=lines[index];
      if(line.startsWith('```')){closeList();const code=[];index++;while(index<lines.length&&!lines[index].startsWith('```'))code.push(lines[index++]);out.push('<pre><code>'+escapeHTML(code.join('\n'))+'</code></pre>');index++;continue}
      if(/^\s*\|.*\|\s*$/.test(line)&&index+1<lines.length&&/^\s*\|?\s*:?-+/.test(lines[index+1])){closeList();const rows=[];rows.push(line);index+=2;while(index<lines.length&&/^\s*\|.*\|\s*$/.test(lines[index]))rows.push(lines[index++]);const cells=row=>row.trim().replace(/^\||\|$/g,'').split('|').map(cell=>inline(cell.trim()));out.push('<div class="md-table"><table><thead><tr>'+cells(rows[0]).map(cell=>'<th>'+cell+'</th>').join('')+'</tr></thead><tbody>'+rows.slice(1).map(row=>'<tr>'+cells(row).map(cell=>'<td>'+cell+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>');continue}
      const heading=line.match(/^(#{1,4})\s+(.+)$/);if(heading){closeList();out.push('<h'+heading[1].length+'>'+inline(heading[2])+'</h'+heading[1].length+'>');index++;continue}
      if(/^>\s?/.test(line)){closeList();out.push('<blockquote>'+inline(line.replace(/^>\s?/,''))+'</blockquote>');index++;continue}
      const bullet=line.match(/^\s*[-*+]\s+(.+)$/),ordered=line.match(/^\s*\d+[.)]\s+(.+)$/);
      if(bullet||ordered){const wanted=ordered?'ol':'ul';if(list!==wanted){closeList();list=wanted;out.push('<'+list+'>')}out.push('<li>'+inline((bullet||ordered)[1])+'</li>');index++;continue}
      if(!line.trim()){closeList();index++;continue}
      closeList();const paragraph=[line.trim()];index++;while(index<lines.length&&lines[index].trim()&&!/^(#{1,4})\s+|^>|^\s*[-*+]\s+|^\s*\d+[.)]\s+|^```|^\s*\|.*\|\s*$/.test(lines[index]))paragraph.push(lines[index++].trim());out.push('<p>'+inline(paragraph.join(' '))+'</p>')
    }
    closeList();return out.join('');
  }
  function markdownDiff(value,peerValues){
    const html=markdown(value),peers=(peerValues||[]).filter(item=>typeof item==='string');
    if(!peers.length)return html;
    const selector='h1,h2,h3,h4,p,li,blockquote,pre,th,td',normalize=text=>String(text||'').replace(/\s+/g,' ').trim();
    const describe=root=>{let section='';return [...root.querySelectorAll(selector)].map(node=>{const text=normalize(node.textContent),heading=/^H[1-4]$/.test(node.tagName);if(heading)section=text;return{node,key:text?(heading?'heading|'+text:section+'|'+node.tagName+'|'+text):''}}).filter(item=>item.key)};
    const blockSet=source=>{const template=document.createElement('template');template.innerHTML=markdown(source);return new Set(describe(template.content).map(item=>item.key))};
    const peerSets=peers.map(blockSet),template=document.createElement('template');template.innerHTML=html;
    describe(template.content).forEach(item=>{if(peerSets.some(set=>!set.has(item.key)))item.node.classList.add('sandbox-diff-block')});
    return template.innerHTML;
  }
  function def(experiment){
    if(!experiment)return null;
    return byId.get(experiment.experimentId||experiment.id)||SB.experiments.find(item=>item.session===experiment.session&&item.data===experiment.data&&item.optimizer===experiment.optimizer&&item.user===experiment.user)||null;
  }
  function record(experiment,version,caseIndex){const item=def(experiment),caseId=SB.cases[caseIndex]?.[0];return item&&caseId?SB.records[item.id+'|'+version+'|'+caseId]:null}
  function versionMetric(experiment,version){const item=def(experiment);return item?SB.versionMetrics[item.id+'|'+version]:null}
  function safeMetric(value){return value||{missing:true,total:0,dims:SB.dimensions.map(()=>0),red:0,caseCount:0}}
  function dataFor(caseIndex,experiment){const item=def(experiment),caseId=SB.cases[caseIndex]?.[0];return SB.caseDataByExperiment?.[item?.id+'|'+caseId]||SB.caseData[caseIndex]||{sample:'—',range:'—',scope:'—',questions:[],files:[],metadata:{},rawCase:{}}}
  function experimentFromSnapshot(item){return{experimentId:item.id,session:item.session,user:item.user,data:item.data,optimizer:item.optimizer,version:item.latestVersion}}
  function traceDuration(ms){if(!ms)return '—';const seconds=Math.round(ms/1000);return seconds>=60?Math.floor(seconds/60)+'m '+String(seconds%60).padStart(2,'0')+'s':seconds+'s'}
  function fileIcon(type){
    const icons={PDF:'PDF',XLS:'XLS',XLSX:'XLS',CSV:'CSV',DOC:'DOC',DOCX:'DOC',PPT:'PPT',PPTX:'PPT',PNG:'IMG',JPG:'IMG',JPEG:'IMG',GIF:'IMG',MP3:'AUD',WAV:'AUD',ZIP:'ZIP',TXT:'TXT',MD:'MD'};
    return icons[String(type||'').toUpperCase()]||'FILE';
  }
  function sourceFileAnchor(file,className){
    const path=String(file?.[0]||'未命名文件'),type=String(file?.[1]||'FILE').toUpperCase(),href=String(file?.[2]||'#');
    const parts=path.split('/'),name=parts.pop()||path,folder=parts.join(' / ');
    return '<a class="'+(className||'source-file-chip')+'" href="'+escapeHTML(href)+'" target="_blank" title="'+escapeHTML(path)+'"><span class="source-file-type type-'+escapeHTML(type.toLowerCase())+'">'+fileIcon(type)+'</span><span class="source-file-copy"><b>'+escapeHTML(name)+'</b>'+(folder?'<small>'+escapeHTML(folder)+'</small>':'')+'</span><i>'+escapeHTML(type)+' ↗</i></a>';
  }
  function sourceSummary(files){
    const counts={};files.forEach(file=>{const type=String(file?.[1]||'FILE').toUpperCase();counts[type]=(counts[type]||0)+1});
    return Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([type,count])=>'<span>'+escapeHTML(type)+' '+count+'</span>').join('');
  }
  function sourceLinks(caseIndex,experiment){
    const files=dataFor(caseIndex,experiment).files||[];
    return files.length?files.map(file=>sourceFileAnchor(file,'source-file-chip')).join(''):'<span class="source-empty">静态快照未收录原始资料包</span>';
  }
  function metadataTopLevelCount(value){
    if(Array.isArray(value))return value.length+' 个顶层数组项';
    if(value&&typeof value==='object')return Object.keys(value).length+' 个顶层字段';
    return '1 个原始值';
  }
  function metadataValueHTML(value,depth){
    const level=depth||0;
    if(value===null)return '<span class="metadata-value metadata-null">null</span>';
    if(value===undefined)return '<span class="metadata-value metadata-null">undefined</span>';
    if(Array.isArray(value)){
      if(!value.length)return '<span class="metadata-value metadata-empty">[] · 空数组</span>';
      const body='<ol class="metadata-tree-list">'+value.map((item,index)=>'<li><span class="metadata-index">'+index+'</span>'+metadataValueHTML(item,level+1)+'</li>').join('')+'</ol>';
      return level?'<details class="metadata-tree-node" open><summary><span>Array</span><em>'+value.length+' 项</em></summary>'+body+'</details>':body;
    }
    if(typeof value==='object'){
      const entries=Object.entries(value);
      if(!entries.length)return '<span class="metadata-value metadata-empty">{} · 空对象</span>';
      const body='<dl class="metadata-tree">'+entries.map(([key,item])=>'<div class="metadata-tree-row"><dt>'+escapeHTML(key)+'</dt><dd>'+metadataValueHTML(item,level+1)+'</dd></div>').join('')+'</dl>';
      return level?'<details class="metadata-tree-node" open><summary><span>Object</span><em>'+entries.length+' 个字段</em></summary>'+body+'</details>':body;
    }
    if(typeof value==='boolean')return '<span class="metadata-value metadata-boolean">'+String(value)+'</span>';
    if(typeof value==='number')return '<span class="metadata-value metadata-number">'+String(value)+'</span>';
    return '<span class="metadata-value metadata-string">'+escapeHTML(value)+'</span>';
  }
  function evidenceText(value){
    if(value===null||value===undefined)return '—';
    if(typeof value==='string')return value;
    try{return JSON.stringify(value,null,2)}catch(error){return String(value)}
  }
  function evidenceItemsHTML(items){
    if(!Array.isArray(items)||!items.length)return '<div class="metadata-empty-block">该 Case 没有 evidence_metadata.json items</div>';
    return '<div class="metadata-evidence-list">'+items.map((rawItem,index)=>{
      const item=rawItem&&typeof rawItem==='object'?rawItem:{content:rawItem};
      const id=evidenceText(item.id||('EV-'+String(index+1).padStart(3,'0')));
      const type=evidenceText(item.type);
      return '<article class="metadata-evidence-card"><header><span class="metadata-evidence-index">'+(index+1)+'</span><div class="metadata-evidence-id"><small>id</small><b>'+escapeHTML(id)+'</b></div><div class="metadata-evidence-type"><small>type</small><b>'+escapeHTML(type)+'</b></div></header><dl><div class="metadata-evidence-source"><dt>source_ref</dt><dd>'+escapeHTML(evidenceText(item.source_ref))+'</dd></div><div class="metadata-evidence-content"><dt>content</dt><dd>'+escapeHTML(evidenceText(item.content))+'</dd></div></dl></article>';
    }).join('')+'</div>';
  }
  window.OPENHARNESS_SANDBOX_ADAPTER=function(){
    SB=window.OPENHARNESS_SANDBOX;
    if(!SB)throw new Error('OPENHARNESS_SANDBOX snapshot is missing');
    byId=new Map(SB.experiments.map(item=>[item.id,item]));
    sessions=SB.sessions;
    dataTypes=SB.dataTypes;
    optimizers=SB.optimizers;
    users=SB.users; judges=SB.judges||[["v1","Judge V1"],["v2","Judge V2"],["v3","Judge V3"]];
    dims=SB.dimensions.map(item=>item.label);
    cases=SB.cases;
    caseData=SB.caseData;
    rubrics=SB.rubrics;
    versions=function(experiment){const item=def(experiment);return item?item.versions.slice():[]};
    versionParents=function(experiment){const item=def(experiment);return item?item.parents:{}};
    parentVersion=function(experiment,version){return versionParents(experiment)[version]||'—'};
    metric=function(experiment,version){return safeMetric(versionMetric(experiment,version))};
    caseMetric=function(experiment,version,caseIndex){return safeMetric(record(experiment,version,caseIndex))};
    rubricMetric=function(experiment,version,caseIndex,rubricIndex){
      const rec=record(experiment,version,caseIndex),rubric=rubrics[rubricIndex],raw=Number(rec?.checks?.[rubric[0]]??0),redline=rubric[4]==='红线';
      return{value:redline?(raw===1?'PASS':'FAIL'):1+4*raw,bad:redline?raw<1:raw===0,reason:rec?.reasoning?.[rubric[0]]||'Judge 未返回文字理由。'};
    };
    renderLatestOverview=function(){
      let rows=[],selectedCount=state.experiments.length,f=state.latestFilters;
      SB.experiments.forEach(item=>{if(f.session!=='all'&&f.session!==item.session||f.user!=='all'&&f.user!==item.user||f.data!=='all'&&f.data!==item.data||f.optimizer!=='all'&&f.optimizer!==item.optimizer||f.judge!=='all'&&f.judge!==item.judge)return;let e=experimentFromSnapshot(item),v=item.latestVersion,m=metric(e,v),selected=state.experiments.some(x=>def(x)?.id===item.id),disabled=!selected&&selectedCount>=3;rows.push('<tr class="'+(selected?'selected ':'')+(disabled?'disabled':'')+'" data-latest-session="'+item.session+'" data-latest-user="'+item.user+'" data-latest-data="'+item.data+'" data-latest-optimizer="'+item.optimizer+'" data-latest-judge="'+item.judge+'" aria-selected="'+selected+'"><td><button class="latest-pick '+(selected?'selected':'')+'" '+(disabled?'disabled':'')+'>'+(selected?'✓ 已选':disabled?'已达上限':'+ 添加')+'</button></td><td>'+escapeHTML(item.sessionLabel)+'</td><td>'+escapeHTML(item.userLabel)+'</td><td>'+escapeHTML(item.dataLabel)+'</td><td>'+escapeHTML(item.optimizerLabel)+'</td><td>'+escapeHTML(item.judgeLabel)+'</td><td class="latest-version">'+v+'</td><td class="score">'+m.total.toFixed(2)+'</td><td class="'+(m.red?'red':'score')+'">'+m.red+'</td>'+m.dims.map(x=>'<td class="score">'+x.toFixed(1)+'</td>').join('')+'</tr>')});
      document.querySelector('#latestOverview').innerHTML='<div class="latest-head"><div><b>最新评测表现</b><small>'+SB.meta.judgmentCount+' 条 Case×版本 Judgment · '+SB.meta.checkCount+' 条 Check；点击一行加入下方实验</small></div><span class="latest-count">已选 '+selectedCount+' / 3 · 当前 '+rows.length+' 条</span></div><div class="latest-wrap"><table class="latest-table"><thead><tr><th>选择</th><th><label class="latest-filter-head"><span>会话</span><select data-latest-filter="session">'+latestFilterOptions(sessions,f.session)+'</select></label></th><th><label class="latest-filter-head"><span>用户</span><select data-latest-filter="user">'+latestFilterOptions(users,f.user)+'</select></label></th><th><label class="latest-filter-head"><span>Data 类型</span><select data-latest-filter="data">'+latestFilterOptions(dataTypes,f.data)+'</select></label></th><th><label class="latest-filter-head"><span>Optimizer</span><select data-latest-filter="optimizer">'+latestFilterOptions(optimizers,f.optimizer)+'</select></label></th><th><label class="latest-filter-head"><span>Judge</span><select data-latest-filter="judge">'+latestFilterOptions(judges,f.judge)+'</select></label></th><th>最新版本</th><th>总分</th><th>红线</th>'+dims.map(d=>'<th>'+d+'</th>').join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table></div>';
    };
    reportSideDataHTML=function(experiment,version,caseIndex,metadataKey){const d=dataFor(caseIndex,experiment),files=d.files||[];return '<section class="report-side-data"><div class="report-side-data-head"><div><b>数据展示</b><small>'+files.length+' 个原始文件</small></div><button class="report-side-metadata" data-open-metadata="'+metadataKey+'">Metadata ↗</button></div><div class="source-type-summary">'+sourceSummary(files)+'</div><div class="report-side-source-list">'+sourceLinks(caseIndex,experiment)+'</div></section>'};
    caseDataDrawer=function(experiment,version,caseIndex,key){const d=dataFor(caseIndex,experiment),files=d.files||[],fileHTML=files.map(file=>sourceFileAnchor(file,'case-source-link')).join('');return '<tr class="single-case-data-row"><td colspan="10"><div class="case-data-drawer"><div class="case-data-head"><b>数据展示 · '+escapeHTML(cases[caseIndex][0])+'</b><small>'+escapeHTML(selectedLabel(dataTypes,experiment.data))+' · '+escapeHTML(version)+'</small></div><div class="case-data-grid"><button class="case-metadata-card" data-open-metadata="'+key+'"><b>Metadata</b><span>'+escapeHTML(d.sample)+'；'+escapeHTML(d.scope)+'</span><em>查看完整 Metadata →</em></button><section class="case-source-package"><div class="source-package-head"><h4>原始资料包 · '+files.length+' 个文件</h4><div class="source-type-summary">'+sourceSummary(files)+'</div></div><div class="case-source-list">'+(fileHTML||'<div class="source-empty">当前实验未收录可访问的原始资料文件</div>')+'</div></section></div></div></td></tr>'};    compareCaseDataDrawer=function(experiment,experimentIndex,version,caseIndex){return caseDataDrawer(experiment,version,caseIndex,experimentIndex+'|'+version+'|'+caseIndex).replace(/^<tr[^>]*><td[^>]*>|<\/td><\/tr>$/g,'')};
    openMetadata=function(version,caseIndex,experimentIndex){let experiment=isVersionCompareMode()?Object.assign({},state.experiments[0],{version}):(state.experiments[experimentIndex||0]||state.experiments[0]),d=dataFor(caseIndex,experiment),m=caseMetric(experiment,version,caseIndex),rawMetadata=Object.prototype.hasOwnProperty.call(d,'rawMetadata')?d.rawMetadata:(d.metadata??{}),rawJSON,metadataSource=d.metadataSource||'state.json · case.metadata',evidenceItems=rawMetadata&&Array.isArray(rawMetadata.items)?rawMetadata.items:[],metadataBadge=evidenceItems.length+' 条 Evidence',rawBadge=metadataTopLevelCount(rawMetadata);try{rawJSON=JSON.stringify(rawMetadata,null,2);if(rawJSON===undefined)rawJSON='undefined'}catch(error){rawJSON=String(rawMetadata)}const files=d.files||[],fields=[['Case ID',cases[caseIndex][0]],['主题',cases[caseIndex][1]],['Data 类型',selectedLabel(dataTypes,experiment.data)],['Skill 版本',version],['来源文件数',files.length+' 个'],['Evidence Items',String(evidenceItems.length)],['当前总分',m.missing?'未评测':m.total.toFixed(2)],['红线数量',m.missing?'未评测':String(m.red)],['Metadata 来源',metadataSource]];document.querySelector('#metadataTitle').textContent='Metadata · '+cases[caseIndex][0];document.querySelector('#metadataContent').innerHTML='<div class="metadata-hero"><b>'+escapeHTML(cases[caseIndex][1])+'</b><span>'+escapeHTML(selectedLabel(sessions,experiment.session))+' · '+escapeHTML(selectedLabel(optimizers,experiment.optimizer))+'</span></div><section class="metadata-section"><h4>当前实验定位</h4><div class="metadata-grid">'+fields.map(field=>'<div class="metadata-field"><small>'+escapeHTML(field[0])+'</small><b>'+escapeHTML(field[1])+'</b></div>').join('')+'</div></section><section class="metadata-section metadata-evidence-section"><div class="metadata-section-title"><div><h4>Evidence Items</h4><small>按该 Case 的 evidence_metadata.json items 原始顺序展示</small></div><span>'+escapeHTML(metadataBadge)+'</span></div>'+evidenceItemsHTML(evidenceItems)+'</section><section class="metadata-section metadata-raw-section"><div class="metadata-section-title"><div><h4>完整原始 Metadata JSON</h4><small>保留完整原始文档用于校验，不删减、不截断、不改写</small></div><span>'+escapeHTML(rawBadge)+'</span></div><details class="metadata-json"><summary>展开完整 JSON 文本（格式化）</summary><pre>'+escapeHTML(rawJSON||'{}')+'</pre></details></section><section class="metadata-section"><div class="metadata-section-title"><div><h4>原始资料包</h4><small>文件来自该实验 Case 的 input_files，点击可预览 PDF/图片/文本或下载 Office 文件</small></div><span>'+files.length+' 个文件</span></div><div class="source-type-summary metadata-source-summary">'+sourceSummary(files)+'</div><div class="case-source-list metadata-source-list">'+(files.map(file=>sourceFileAnchor(file,'case-source-link')).join('')||'<div class="metadata-empty-block">当前实验未收录可访问的原始资料文件</div>')+'</div></section><section class="metadata-section"><h4>任务输入 / Key Questions</h4>'+(d.questions.length?'<ol class="metadata-list">'+d.questions.map(question=>'<li>'+escapeHTML(question)+'</li>').join('')+'</ol>':'<div class="metadata-empty-block">该 Case 未提供 Key Questions</div>')+'</section>';let modal=document.querySelector('#metadataModal');modal.classList.add('on');modal.setAttribute('aria-hidden','false')};    reportHTML=function(caseIndex,experimentIndex){const experiment=state.experiments[experimentIndex],rec=record(experiment,experiment.version,caseIndex);return markdown(rec?.report||'# 报告缺失\n\n当前 Judgment 未关联到报告文本。')};
    compareReportDocument=function(experiment,version,caseIndex){const rec=record(experiment,version,caseIndex);return '<div class="inline-document">'+markdown(rec?.report||'# 报告缺失\n\n当前 Judgment 未关联到报告文本。')+'</div>'};
    compareSkillDocument=function(experiment,version,diffPeers){
      const item=def(experiment),skill=SB.skills[item?.id+'|'+version];
      const peers=(Array.isArray(diffPeers)?diffPeers:[]).map(peer=>SB.skills[item?.id+'|'+peer]).filter(peer=>peer&&!peer.missing);
      const artifactReady=Boolean(skill&&!skill.missing&&skill.skillMd&&skill.instructionMd),highlight=artifactReady&&peers.length>0;
      const source=skill?.source||'';
      const badge=highlight
        ?'<small class="skill-diff-badge">与已打开版本对比；差异内容以黄色标识</small>'
        :artifactReady
          ?'<small>来源：'+escapeHTML(source)+'</small>'
          :'<small class="artifact-missing">generation_runs 未找到该版本的精确 Skill 产物</small>';
      const skillText=artifactReady?skill.skillMd:'# SKILL.md 缺失\n\ngeneration_runs 中没有与当前实验和版本精确对应的 SKILL.md。';
      const instructionText=artifactReady?skill.instructionMd:'# instruction.md 缺失\n\ngeneration_runs 中没有与当前实验和版本精确对应的 references/instructions.md。';
      return '<div class="skill-doc-stack"><section class="skill-doc-box"><header class="skill-doc-box-head" data-skill-doc-toggle role="button" tabindex="0" aria-expanded="true"><h5>SKILL.md</h5>'+badge+'</header><div class="inline-document">'+markdownDiff(skillText,peers.map(peer=>peer.skillMd||''))+'</div></section><section class="skill-doc-box"><header class="skill-doc-box-head" data-skill-doc-toggle role="button" tabindex="0" aria-expanded="true"><h5>instruction.md</h5><small>'+(highlight?'与已打开版本对比；差异以黄色标识':artifactReady?'来源：'+escapeHTML(source)+'/references/instructions.md':'不使用 state.json 或相似文段替代')+'</small></header><div class="inline-document">'+markdownDiff(instructionText,peers.map(peer=>peer.instructionMd||''))+'</div></section></div>';
    };
    tracePanelHTML=function(experiment,version,caseIndex){
      const rec=record(experiment,version,caseIndex),trace=rec?.trace||{};
      const operations=Array.isArray(trace.operations)?trace.operations:[],rounds=Array.isArray(trace.rounds)?trace.rounds:[];
      const conversation=Array.isArray(trace.conversation)?trace.conversation:[];
      const conversationText=typeof trace.conversationText==='string'?trace.conversationText:'';
      const hasTrace=Boolean(trace.source)&&(operations.length>0||rounds.length>0||conversation.length>0||conversationText);
      const text=value=>escapeHTML(value||'').replace(/\n/g,'<br>');
      const toolSteps=operations.map(op=>'<details class="path-step"><summary><span class="path-kind tool">三级 · 工具调用</span><span>'+escapeHTML(op.name)+' · '+escapeHTML(op.status)+'</span><i class="execution-chevron">›</i></summary><div class="path-detail"><p>Round '+escapeHTML(op.round??'—')+' · '+traceDuration(op.durationMs)+'</p>'+(op.input?'<p><b>Input</b></p><pre>'+escapeHTML(op.input)+'</pre>':'')+(op.result?'<p><b>Result</b></p><pre>'+escapeHTML(op.result)+'</pre>':'')+'</div></details>').join('');
      const roundSteps=rounds.map(round=>'<details class="path-step"><summary><span class="path-kind think">三级 · Agent 轮次</span><span>'+escapeHTML(round.name)+' · '+escapeHTML(round.status)+'</span><i class="execution-chevron">›</i></summary><div class="path-detail">'+(round.output?'<pre>'+escapeHTML(round.output)+'</pre>':'<p>该轮未保存 final_output。</p>')+'<div class="audit-note">仅展示 generation_runs 中保存的可审计输出。</div></div></details>').join('');
      const execution=(roundSteps||toolSteps)?'<details class="agent-execution"><summary><span class="trace-level-badge">二级</span><span>'+traceDuration(trace.durationMs)+'</span><i class="execution-chevron">›</i><span>展开 Agent 执行路径</span></summary><div class="execution-path"><div class="trace-path-heading">执行轮次</div>'+(roundSteps||'<div class="audit-note">generation_runs 中没有 round result。</div>')+'<div class="trace-path-heading">工具调用</div>'+(toolSteps||'<div class="audit-note">generation_runs 中没有 tool operation。</div>')+'</div></details>':'';
      let lastAgent=-1;
      conversation.forEach((message,index)=>{if(message.role==='agent')lastAgent=index});
      let flow=conversation.map((message,index)=>'<div class="trace-msg '+(message.role==='user'?'user':'agent')+'"><div class="trace-who">'+(message.role==='user'?'USER':'AGENT · '+escapeHTML(trace.model||'model 未记录'))+(message.round?' · Round '+message.round:'')+'</div><div class="trace-bubble"><div class="agent-answer">'+text(message.content)+'</div>'+(index===lastAgent?execution:'')+'</div></div>').join('');
      if(conversationText){
        flow+='<div class="trace-msg agent"><div class="trace-who">generation_runs · conversation.md</div><div class="trace-bubble"><pre>'+escapeHTML(conversationText)+'</pre>'+(lastAgent<0?execution:'')+'</div></div>';
      }else if(lastAgent<0&&execution){
        flow+='<div class="trace-msg agent"><div class="trace-who">generation_runs · Agent Trace</div><div class="trace-bubble">'+execution+'</div></div>';
      }
      if(!hasTrace)flow='<div class="trace-empty"><b>生成链路缺失</b><p>generation_runs 中没有与当前实验、版本、generation_id 和 Case 精确对应的 Trace；未使用其他文本替代。</p></div>';
      return '<aside class="trace-panel"><div class="trace-panel-head"><div><b>生成链路 · WB CLI Trace</b><small class="trace-level-note">唯一来源：generation_runs'+(trace.source?' / '+escapeHTML(trace.source):'')+'</small></div><span>'+escapeHTML(trace.status||'missing')+' · '+traceDuration(trace.durationMs)+'</span></div><div class="trace-flow">'+flow+'</div></aside>';
    };    compareDataPackage=function(){return ''};
  };
})();
