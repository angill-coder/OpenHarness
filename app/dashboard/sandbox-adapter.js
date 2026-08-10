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
  function markdownDiff(value,peerValue,documentKind,relation){
    const html=markdown(value),peer=Array.isArray(peerValue)?peerValue.find(item=>typeof item==='string'):peerValue;
    if(typeof peer!=='string')return html;
    const selector='h1,h2,h3,h4,p,li,blockquote,pre,th,td',normalize=text=>String(text||'').replace(/\s+/g,' ').trim();
    const describe=root=>[...root.querySelectorAll(selector)].map(node=>({node,key:normalize(node.textContent),tag:node.tagName})).filter(item=>item.key);
    const currentTemplate=document.createElement('template'),peerTemplate=document.createElement('template');
    currentTemplate.innerHTML=html;peerTemplate.innerHTML=markdown(peer);
    const current=describe(currentTemplate.content),baseline=describe(peerTemplate.content);
    const rows=current.length+1,cols=baseline.length+1,table=Array.from({length:rows},()=>new Uint16Array(cols));
    for(let i=current.length-1;i>=0;i--)for(let j=baseline.length-1;j>=0;j--){
      table[i][j]=current[i].key===baseline[j].key&&current[i].tag===baseline[j].tag
        ?table[i+1][j+1]+1:Math.max(table[i+1][j],table[i][j+1]);
    }
    const matches=[];let i=0,j=0;
    while(i<current.length&&j<baseline.length){
      if(current[i].key===baseline[j].key&&current[i].tag===baseline[j].tag){matches.push([i,j]);i++;j++}
      else if(table[i+1][j]>=table[i][j+1])i++;else j++;
    }
    const anchors=[[-1,-1],...matches,[current.length,baseline.length]];
    const mark=(item,kind,slot)=>{
      item.node.classList.add('sandbox-diff-block','skill-diff-'+kind);
      item.node.dataset.skillDiffKind=kind;
      item.node.dataset.skillDiffSlot=(documentKind||'document')+'-'+slot;
    };
    const similarity=(left,right)=>{
      if(left.tag!==right.tag)return 0;
      const grams=text=>{const compact=normalize(text).toLowerCase().replace(/\s/g,'');if(compact.length<2)return new Set([compact]);const result=new Set();for(let k=0;k<compact.length-1;k++)result.add(compact.slice(k,k+2));return result};
      const a=grams(left.key),b=grams(right.key);let overlap=0;a.forEach(token=>{if(b.has(token))overlap++});
      return a.size+b.size?2*overlap/(a.size+b.size):0;
    };
    const pairChanges=(oldItems,newItems)=>{
      const candidates=[];
      oldItems.forEach((oldItem,oldIndex)=>newItems.forEach((newItem,newIndex)=>{const score=similarity(oldItem,newItem);if(score>=0.28)candidates.push({oldIndex,newIndex,score})}));
      candidates.sort((a,b)=>b.score-a.score||Math.abs(a.oldIndex-a.newIndex)-Math.abs(b.oldIndex-b.newIndex));
      const usedOld=new Set(),usedNew=new Set(),chosen=[];
      candidates.forEach(candidate=>{if(!usedOld.has(candidate.oldIndex)&&!usedNew.has(candidate.newIndex)){usedOld.add(candidate.oldIndex);usedNew.add(candidate.newIndex);chosen.push(candidate)}});
      return {chosen,usedOld,usedNew};
    };
    for(let segment=0;segment<anchors.length-1;segment++){
      const [leftCurrent,leftBase]=anchors[segment],[rightCurrent,rightBase]=anchors[segment+1];
      const currentSegment=current.slice(leftCurrent+1,rightCurrent),baseSegment=baseline.slice(leftBase+1,rightBase);
      const oldItems=relation==='newer'?baseSegment:currentSegment,newItems=relation==='newer'?currentSegment:baseSegment;
      const pairs=pairChanges(oldItems,newItems);
      pairs.chosen.forEach(pair=>{
        const currentIndex=relation==='newer'?pair.newIndex:pair.oldIndex;
        mark(currentSegment[currentIndex],'modified','change-'+segment+'-'+pair.oldIndex+'-'+pair.newIndex);
      });
      currentSegment.forEach((item,index)=>{
        const matched=relation==='newer'?pairs.usedNew.has(index):pairs.usedOld.has(index);
        if(!matched)mark(item,relation==='newer'?'added':'deleted',(relation==='newer'?'added-':'deleted-')+segment+'-'+index);
      });
    }
    return currentTemplate.innerHTML;
  }
  function def(experiment){
    if(!experiment)return null;
    return byId.get(experiment.experimentId||experiment.id)||SB.experiments.find(item=>item.session===experiment.session&&item.data===experiment.data&&item.optimizer===experiment.optimizer&&item.user===experiment.user)||null;
  }
  function record(experiment,version,caseIndex){const item=def(experiment),caseId=SB.cases[caseIndex]?.[0];return item&&caseId?SB.records[item.id+'|'+version+'|'+caseId]:null}
  function versionMetric(experiment,version){const item=def(experiment);return item?SB.versionMetrics[item.id+'|'+version]:null}
  function safeMetric(value){return value||{missing:true,total:0,dims:SB.dimensions.map(()=>0),red:0,caseCount:0}}
  function dataFor(caseIndex,experiment){const item=def(experiment),caseId=SB.cases[caseIndex]?.[0];return SB.caseDataByExperiment?.[item?.id+'|'+caseId]||SB.caseData[caseIndex]||{sample:'—',range:'—',scope:'—',questions:[],files:[],metadata:{},rawCase:{}}}
  function experimentFromSnapshot(item){return{experimentId:item.id,session:item.session,user:item.user,data:item.data,optimizer:item.optimizer,optimizerModel:item.optimizerModel,judge:item.judge,judgeModel:item.judgeModel,versionModels:item.versionModels||{},version:item.latestVersion}}
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
    if(!Array.isArray(items)||!items.length)return '<div class="metadata-empty-block">该 Case 没有 structured_data.json items</div>';
    return '<div class="metadata-evidence-list">'+items.map((rawItem,index)=>{
      const item=rawItem&&typeof rawItem==='object'?rawItem:{content:rawItem};
      const id=evidenceText(item.id||('EV-'+String(index+1).padStart(3,'0')));
      const type=evidenceText(item.type);
      return '<article class="metadata-evidence-card"><header><span class="metadata-evidence-index">'+(index+1)+'</span><div class="metadata-evidence-id"><small>id</small><b>'+escapeHTML(id)+'</b></div><div class="metadata-evidence-type"><small>type</small><b>'+escapeHTML(type)+'</b></div></header><dl><div class="metadata-evidence-source"><dt>source_ref</dt><dd>'+escapeHTML(evidenceText(item.source_ref))+'</dd></div><div class="metadata-evidence-content"><dt>content</dt><dd>'+escapeHTML(evidenceText(item.content))+'</dd></div></dl></article>';
    }).join('')+'</div>';
  }
  function qualityButtonHTML(data,key){
    const quality=data?.quality||{},score=Number(quality.overall_score);
    const scoreText=quality.available&&Number.isFinite(score)?'<span>'+score.toFixed(1)+'</span>':'';
    const unavailable=quality.available===false?' unavailable':'';
    return '<button class="data-quality-button'+unavailable+'" data-open-quality="'+escapeHTML(key)+'" type="button">\u6570\u636e\u8d28\u91cf\u5f97\u5206'+scoreText+'</button>';
  }
  function openQualityModal(version,caseIndex,experimentIndex){
    const experiment=isVersionCompareMode()
      ?Object.assign({},state.experiments[0],{version})
      :(state.experiments[experimentIndex||0]||state.experiments[0]);
    const data=dataFor(caseIndex,experiment),quality=data.quality||{},scores=quality.scores||{};
    const overallLabel='\u7efc\u5408\u8d28\u91cf\u5206';
    const detailLabels=['\u9057\u6f0f\u8986\u76d6\u5206','\u51b2\u7a81\u4e00\u81f4\u6027\u5206','\u4fe1\u566a\u5206'];
    const numeric=value=>value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value))?Number(value):null;
    const overall=numeric(quality.overall_score)??numeric(scores[overallLabel]);
    const scoreRows=detailLabels.map(label=>{
      const value=numeric(scores[label]),display=value===null?'\u2014':value.toFixed(1),width=value===null?0:Math.max(0,Math.min(100,value));
      return '<div class="quality-subscore-row"><div class="quality-subscore-copy"><b>'+escapeHTML(label)+'</b><span><strong>'+display+'</strong> / 100</span></div><div class="quality-subscore-track"><i style="width:'+width+'%"></i></div></div>';
    }).join('');
    const details=Array.isArray(quality.details)?quality.details:[];
    const body=quality.available
      ?'<section class="quality-overall-card"><div><small>\u7efc\u5408\u8d28\u91cf\u5206</small><b>'+(overall===null?'\u2014':overall.toFixed(1))+'</b><span>/ 100</span></div><p>\u8be5\u5206\u6570\u7531\u4e0b\u65b9\u4e09\u9879\u8d28\u68c0\u5f97\u5206\u52a0\u6743\u8ba1\u7b97</p></section>'+
        '<section class="quality-subscore-section"><div class="quality-section-heading"><h4>\u5f97\u5206\u7ec6\u9879</h4><span>3 \u9879</span></div><div class="quality-subscore-list">'+scoreRows+'</div></section>'+
        (details.length?'<section class="quality-detail-section"><div class="quality-section-heading"><h4>\u8d28\u68c0\u6307\u6807\u660e\u7ec6</h4><span>'+details.length+' \u9879</span></div><dl>'+details.map(row=>'<div><dt>'+escapeHTML(row.label)+'</dt><dd>'+escapeHTML(row.result)+'</dd></div>').join('')+'</dl></section>':'')+
        (quality.formula?'<div class="quality-formula">'+escapeHTML(quality.formula)+'</div>':'')+
        '<div class="quality-source">\u6765\u6e90\uff1a'+escapeHTML(quality.source||'Case \u6570\u636e\u8d28\u68c0\u62a5\u544a')+'</div>'
      :'<div class="quality-empty"><b>\u6682\u65e0\u8d28\u68c0\u62a5\u544a</b><p>\u5f53\u524d Case \u76ee\u5f55\u4e0b\u672a\u627e\u5230\u201c\u6570\u636e\u8d28\u68c0\u62a5\u544a.md\u201d\uff0c\u56e0\u6b64\u6ca1\u6709\u53ef\u5c55\u793a\u7684\u8d28\u68c0\u5206\u6570\u3002</p></div>';
    document.querySelector('#metadataTitle').textContent='\u6570\u636e\u8d28\u91cf\u5f97\u5206 \u00b7 '+cases[caseIndex][0];
    document.querySelector('#metadataContent').innerHTML='<div class="quality-hero"><div><small>CASE DATA QUALITY</small><b>'+escapeHTML(cases[caseIndex][1])+'</b></div><span>'+escapeHTML(selectedLabel(dataTypes,experiment.data))+'</span></div>'+body;
    const modal=document.querySelector('#metadataModal');
    modal.classList.add('on');
    modal.setAttribute('aria-hidden','false');
  }

  const textFromCodes=(...codes)=>String.fromCodePoint(...codes);
  const rubricGuideCache=new Map();
  const closeRubricGuide=()=>{
    const modal=document.querySelector('#rubricGuideModal');
    if(!modal)return;
    modal.classList.remove('on');
    modal.setAttribute('aria-hidden','true');
  };
  const applyRubricGuideLayout=()=>{
    const modal=document.querySelector('#rubricGuideModal');
    const card=modal&&modal.querySelector('.rubric-guide-modal-card');
    const head=modal&&modal.querySelector('.rubric-guide-modal-head');
    const content=document.querySelector('#rubricGuideContent');
    if(!modal||!card||!content)return;
    const force=(element,styles)=>Object.entries(styles).forEach(([name,value])=>
      element.style.setProperty(name,value,'important'));
    force(modal,{padding:'8px','align-items':'stretch','justify-content':'center'});
    force(card,{
      display:'flex','flex-direction':'column',
      width:'min(1380px,calc(100vw - 16px))',
      height:'calc(100dvh - 16px)','max-height':'calc(100dvh - 16px)',
      'min-height':'calc(100dvh - 16px)',margin:'auto'
    });
    if(head)force(head,{flex:'0 0 50px','min-height':'50px'});
    force(content,{
      flex:'1 1 auto',height:'0','min-height':'0','max-height':'none',
      overflow:'auto',background:'#0b1017',color:'#edf3fa'
    });
    const documentNode=content.querySelector('.rubric-guide-document');
    if(!documentNode)return;
    force(documentNode,{'min-height':'100%',background:'#141d27',color:'#e9f0f8'});
    documentNode.querySelectorAll('p,li,dd,dt,span').forEach(node=>
      force(node,{background:'transparent',color:'#e5edf7',opacity:'1'}));
    documentNode.querySelectorAll('blockquote').forEach(node=>
      force(node,{background:'#1d3042',color:'#edf6ff','border-left':'4px solid #7fc3ff'}));
    documentNode.querySelectorAll('pre').forEach(node=>
      force(node,{background:'#080f17',color:'#e3efff',border:'1px solid #485d75'}));
    documentNode.querySelectorAll('code').forEach(node=>
      force(node,{background:'#09121c',color:'#b9ddff','border-color':'#506b89'}));
    documentNode.querySelectorAll('.md-table').forEach(node=>
      force(node,{background:'transparent',color:'#e5edf7'}));
    documentNode.querySelectorAll('table').forEach(node=>
      force(node,{background:'#101821',color:'#eef4fb','border-color':'#53677f'}));
    documentNode.querySelectorAll('th').forEach(node=>
      force(node,{background:'#293b50',color:'#fff','border-color':'#60758e'}));
    documentNode.querySelectorAll('td').forEach(node=>
      force(node,{background:'#192531',color:'#e5edf7','border-color':'#4a5e75'}));
    documentNode.querySelectorAll('hr').forEach(node=>
      force(node,{background:'transparent','border-color':'#52657b'}));
  };
  const openRubricGuide=async(sessionId)=>{
    const modal=document.querySelector('#rubricGuideModal');
    const content=document.querySelector('#rubricGuideContent');
    if(!modal||!content||!sessionId)return;
    modal.classList.add('on');
    applyRubricGuideLayout();
    modal.setAttribute('aria-hidden','false');
    content.innerHTML='<div class="rubric-guide-loading">'+textFromCodes(0x6B63,0x5728,0x8BFB,0x53D6)+' Rubric '+textFromCodes(0x8BC4,0x5206,0x89C4,0x5219,0x2026)+'</div>';
    try{
      let payload=rubricGuideCache.get(sessionId);
      if(!payload){
        const response=await fetch('/api/local/rubric-guide?session='+encodeURIComponent(sessionId));
        payload=await response.json();
        if(!response.ok)throw new Error(payload.error||('HTTP '+response.status));
        rubricGuideCache.set(sessionId,payload);
      }
      content.innerHTML='<article class="inline-document rubric-guide-document">'+markdown(payload.markdown||'')+'</article>';
      applyRubricGuideLayout();
      content.scrollTop=0;
    }catch(error){
      content.innerHTML='<div class="metadata-empty-block">'+textFromCodes(0x8BC4,0x5206,0x4F9D,0x636E,0x52A0,0x8F7D,0x5931,0x8D25,0xFF1A)+escapeHTML(error.message)+'</div>';
    }
  };
  document.addEventListener('click',event=>{
    const trigger=event.target.closest('.rubric-guide-button[data-rubric-guide]');
    if(!trigger)return;
    event.preventDefault();
    openRubricGuide(trigger.dataset.rubricGuide);
  });
  document.querySelector('#rubricGuideClose')?.addEventListener('click',closeRubricGuide);
  document.querySelector('#rubricGuideModal')?.addEventListener('click',event=>{
    if(event.target.id==='rubricGuideModal')closeRubricGuide();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape')closeRubricGuide();
  });
  window.openQualityModal=openQualityModal;
  window.openRubricGuide=openRubricGuide;
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
    if(!evaluationPanelHTML.__rubricGuideWrapped){
      const evaluationPanelWithRubricGuide=evaluationPanelHTML;
      const wrappedEvaluationPanel=function(...args){
        const template=document.createElement('template');
        template.innerHTML=evaluationPanelWithRubricGuide(...args);
        const experiment=state.experiments[Number(args[1])||0]||state.experiments[0];
        const sessionId=experiment?.session||'';
        const title=template.content.querySelector('.eval-list-title');
        if(title&&sessionId){
          const button=document.createElement('button');
          button.type='button';
          button.className='rubric-guide-button';
          button.dataset.rubricGuide=sessionId;
          const buttonStyle={
            appearance:'none',
            display:'inline-flex',
            'align-items':'center',
            'justify-content':'center',
            flex:'0 0 auto',
            'min-width':'max-content',
            margin:'0 0 0 auto',
            border:'1px solid #42658d',
            'border-radius':'999px',
            background:'#1f3046',
            color:'#f4f8ff',
            padding:'6px 13px',
            'font-size':'12px',
            'font-weight':'750',
            cursor:'pointer'
          };
          Object.entries(buttonStyle).forEach(([name,value])=>button.style.setProperty(name,value,'important'));
          button.textContent=textFromCodes(0x67E5,0x770B,0x8BC4,0x5206,0x89C4,0x5219);
          title.appendChild(button);
          title.style.setProperty('display','flex','important');
          title.style.setProperty('align-items','center','important');
          title.style.setProperty('width','100%','important');
          title.style.setProperty('gap','12px','important');
        }
        return template.innerHTML;
      };
      wrappedEvaluationPanel.__rubricGuideWrapped=true;
      evaluationPanelHTML=wrappedEvaluationPanel;
    }
    versions=function(experiment){const item=def(experiment);return item?item.versions.slice():[]};
    versionParents=function(experiment){const item=def(experiment);return item?item.parents:{}};
    parentVersion=function(experiment,version){return versionParents(experiment)[version]||'—'};
    metric=function(experiment,version){return safeMetric(versionMetric(experiment,version))};
    caseMetric=function(experiment,version,caseIndex){return safeMetric(record(experiment,version,caseIndex))};
    versionCaseIndexes=function(experiment,version){const item=def(experiment),ids=SB.experimentVersionCaseIds?.[item?.id+'|'+version]||SB.experimentCaseIds?.[item?.id]||[];return ids.map(id=>SB.cases.findIndex(row=>row[0]===id)).filter(index=>index>=0)};
    rubricMetric=function(experiment,version,caseIndex,rubricIndex){
      const rec=record(experiment,version,caseIndex),rubric=rubrics[rubricIndex],raw=Number(rec?.checks?.[rubric[0]]??0),redline=rubric[4]==='红线';
      return{value:redline?(raw===1?'PASS':'FAIL'):1+4*raw,bad:redline?raw<1:raw===0,reason:rec?.reasoning?.[rubric[0]]||'Judge 未返回文字理由。'};
    };
    renderLatestOverview=function(){
      let rows=[],selectedCount=state.experiments.length,f=state.latestFilters;
      SB.experiments.forEach(item=>{if(f.session!=='all'&&f.session!==item.session||f.user!=='all'&&f.user!==item.user||f.data!=='all'&&f.data!==item.data||f.optimizer!=='all'&&f.optimizer!==item.optimizer||f.judge!=='all'&&f.judge!==item.judge)return;let e=experimentFromSnapshot(item),v=item.latestVersion,m=metric(e,v),selected=state.experiments.some(x=>def(x)?.id===item.id),disabled=!selected&&selectedCount>=3;rows.push('<tr class="'+(selected?'selected ':'')+(disabled?'disabled':'')+'" data-latest-session="'+item.session+'" data-latest-user="'+item.user+'" data-latest-data="'+item.data+'" data-latest-optimizer="'+item.optimizer+'" data-latest-judge="'+item.judge+'" aria-selected="'+selected+'"><td><button class="latest-pick '+(selected?'selected':'')+'" '+(disabled?'disabled':'')+'>'+(selected?'✓ 已选':disabled?'已达上限':'+ 添加')+'</button></td><td>'+escapeHTML(item.sessionLabel)+'</td><td>'+escapeHTML(item.userLabel)+'</td><td>'+escapeHTML(item.dataLabel)+'</td><td>'+escapeHTML(item.optimizerLabel)+'</td><td>'+escapeHTML(item.judgeLabel)+'</td><td class="latest-version">'+v+'</td><td class="score">'+m.total.toFixed(2)+'</td><td class="'+(m.red?'red':'score')+'">'+m.red+'</td>'+m.dims.map(x=>'<td class="score">'+x.toFixed(1)+'</td>').join('')+'</tr>')});
      document.querySelector('#latestHint').textContent=SB.meta.judgmentCount+' 条 Case×版本 Judgment · '+SB.meta.checkCount+' 条 Check；点击一行加入下方评测与对比区域';
      document.querySelector('#latestOverviewBody').innerHTML='<div class="latest-wrap"><table class="latest-table"><thead><tr><th><div class="latest-selection-head"><span>选择</span><span class="latest-count" id="latestCount">已选 '+selectedCount+' / 3 · 当前 '+rows.length+' 条</span></div></th><th><label class="latest-filter-head"><span>会话</span><select data-latest-filter="session">'+latestFilterOptions(sessions,f.session)+'</select></label></th><th><label class="latest-filter-head"><span>用户</span><select data-latest-filter="user">'+latestFilterOptions(users,f.user)+'</select></label></th><th><label class="latest-filter-head"><span>Data 类型</span><select data-latest-filter="data">'+latestFilterOptions(dataTypes,f.data)+'</select></label></th><th><label class="latest-filter-head"><span>Optimizer</span><select data-latest-filter="optimizer">'+latestFilterOptions(optimizers,f.optimizer)+'</select></label></th><th><label class="latest-filter-head"><span>Judge</span><select data-latest-filter="judge">'+latestFilterOptions(judges,f.judge)+'</select></label></th><th>最新版本</th><th>总分</th><th>红线</th>'+dims.map(d=>'<th>'+d+'</th>').join('')+'</tr></thead><tbody>'+rows.join('')+'</tbody></table></div>';
    };
    reportSideDataHTML=function(experiment,version,caseIndex,metadataKey){const d=dataFor(caseIndex,experiment),files=d.files||[];return '<section class="report-side-data"><div class="report-side-data-head"><div><b>数据展示</b><small>'+files.length+' 个原始文件</small></div><button class="report-side-metadata" data-open-metadata="'+metadataKey+'">Structured Data ↗</button></div><div class="source-type-summary">'+sourceSummary(files)+'</div><div class="report-side-source-list">'+sourceLinks(caseIndex,experiment)+'</div></section>'};
    caseDataDrawer=function(experiment,version,caseIndex,key){const d=dataFor(caseIndex,experiment),files=d.files||[],fileHTML=files.map(file=>sourceFileAnchor(file,'case-source-link')).join('');return '<tr class="single-case-data-row"><td colspan="10"><div class="case-data-drawer"><div class="case-data-head"><b>数据展示 · '+escapeHTML(cases[caseIndex][0])+'</b><small>'+escapeHTML(selectedLabel(dataTypes,experiment.data))+' · '+escapeHTML(version)+'</small></div><div class="case-data-grid"><button class="case-metadata-card" data-open-metadata="'+key+'"><b>Structured Data</b><span>'+escapeHTML(d.sample)+'；'+escapeHTML(d.scope)+'</span><em>查看完整 Structured Data →</em></button><section class="case-source-package"><div class="source-package-head"><h4>原始资料包 · '+files.length+' 个文件</h4><div class="source-type-summary">'+sourceSummary(files)+'</div></div><div class="case-source-list">'+(fileHTML||'<div class="source-empty">当前实验未收录可访问的原始资料文件</div>')+'</div></section></div></div></td></tr>'};    compareCaseDataDrawer=function(experiment,experimentIndex,version,caseIndex){return caseDataDrawer(experiment,version,caseIndex,experimentIndex+'|'+version+'|'+caseIndex).replace(/^<tr[^>]*><td[^>]*>|<\/td><\/tr>$/g,'')};
    const reportSideDataBase=reportSideDataHTML;
    reportSideDataHTML=function(experiment,version,caseIndex,key){
      const data=dataFor(caseIndex,experiment);
      return reportSideDataBase(experiment,version,caseIndex,key)
        .replace('<div class="source-type-summary">','<div class="report-side-quality-row"><div class="source-type-summary">')
        .replace('</div><div class="report-side-source-list">','</div>'+qualityButtonHTML(data,key)+'</div><div class="report-side-source-list">');
    };
    const caseDataDrawerBase=caseDataDrawer;
    caseDataDrawer=function(experiment,version,caseIndex,key){
      const data=dataFor(caseIndex,experiment);
      return caseDataDrawerBase(experiment,version,caseIndex,key)
        .replace('<div class="source-package-head"><h4>','<div class="source-package-head"><div><h4>')
        .replace('</div></div><div class="case-source-list">','</div></div>'+qualityButtonHTML(data,key)+'</div><div class="case-source-list">');
    };

    openMetadata=function(version,caseIndex,experimentIndex){let experiment=isVersionCompareMode()?Object.assign({},state.experiments[0],{version}):(state.experiments[experimentIndex||0]||state.experiments[0]),d=dataFor(caseIndex,experiment),m=caseMetric(experiment,version,caseIndex),rawMetadata=Object.prototype.hasOwnProperty.call(d,'rawMetadata')?d.rawMetadata:(d.metadata??{}),rawJSON,metadataSource=d.metadataSource||'state.json · case.metadata',evidenceItems=rawMetadata&&Array.isArray(rawMetadata.items)?rawMetadata.items:[],metadataBadge=evidenceItems.length+' 条 Evidence',rawBadge=metadataTopLevelCount(rawMetadata);try{rawJSON=JSON.stringify(rawMetadata,null,2);if(rawJSON===undefined)rawJSON='undefined'}catch(error){rawJSON=String(rawMetadata)}const files=d.files||[],fields=[['Case ID',cases[caseIndex][0]],['主题',cases[caseIndex][1]],['Data 类型',selectedLabel(dataTypes,experiment.data)],['Skill 版本',version],['来源文件数',files.length+' 个'],['Evidence Items',String(evidenceItems.length)],['当前总分',m.missing?'未评测':m.total.toFixed(2)],['红线数量',m.missing?'未评测':String(m.red)],['Structured Data 来源',metadataSource]];document.querySelector('#metadataTitle').textContent='Structured Data · '+cases[caseIndex][0];document.querySelector('#metadataContent').innerHTML='<div class="metadata-hero"><b>'+escapeHTML(cases[caseIndex][1])+'</b><span>'+escapeHTML(selectedLabel(sessions,experiment.session))+' · '+escapeHTML(selectedLabel(optimizers,experiment.optimizer))+'</span></div><section class="metadata-section"><h4>当前实验定位</h4><div class="metadata-grid">'+fields.map(field=>'<div class="metadata-field"><small>'+escapeHTML(field[0])+'</small><b>'+escapeHTML(field[1])+'</b></div>').join('')+'</div></section><section class="metadata-section metadata-evidence-section"><div class="metadata-section-title"><div><h4>Evidence Items</h4><small>按该 Case 的 structured_data.json items 原始顺序展示</small></div><span>'+escapeHTML(metadataBadge)+'</span></div>'+evidenceItemsHTML(evidenceItems)+'</section><section class="metadata-section metadata-raw-section"><div class="metadata-section-title"><div><h4>完整原始 Structured Data JSON</h4><small>保留完整原始文档用于校验，不删减、不截断、不改写</small></div><span>'+escapeHTML(rawBadge)+'</span></div><details class="metadata-json"><summary>展开完整 JSON 文本（格式化）</summary><pre>'+escapeHTML(rawJSON||'{}')+'</pre></details></section><section class="metadata-section"><div class="metadata-section-title"><div><h4>原始资料包</h4><small>文件来自该实验 Case 的 input_files，点击可预览 PDF/图片/文本或下载 Office 文件</small></div><span>'+files.length+' 个文件</span></div><div class="source-type-summary metadata-source-summary">'+sourceSummary(files)+'</div><div class="case-source-list metadata-source-list">'+(files.map(file=>sourceFileAnchor(file,'case-source-link')).join('')||'<div class="metadata-empty-block">当前实验未收录可访问的原始资料文件</div>')+'</div></section><section class="metadata-section"><h4>任务输入 / Key Questions</h4>'+(d.questions.length?'<ol class="metadata-list">'+d.questions.map(question=>'<li>'+escapeHTML(question)+'</li>').join('')+'</ol>':'<div class="metadata-empty-block">该 Case 未提供 Key Questions</div>')+'</section>';let modal=document.querySelector('#metadataModal');modal.classList.add('on');modal.setAttribute('aria-hidden','false')};    reportHTML=function(caseIndex,experimentIndex){const experiment=state.experiments[experimentIndex],rec=record(experiment,experiment.version,caseIndex);return markdown(rec?.report||'# 报告缺失\n\n当前 Judgment 未关联到报告文本。')};
    compareReportDocument=function(experiment,version,caseIndex){const rec=record(experiment,version,caseIndex);return '<div class="inline-document">'+markdown(rec?.report||'# 报告缺失\n\n当前 Judgment 未关联到报告文本。')+'</div>'};
    compareSkillDocument=function(experiment,version,diffPeers){
      const item=def(experiment),skill=SB.skills[item?.id+'|'+version];
      const versionRank=value=>{const match=String(value||'').match(/\d+/);return match?Number(match[0]):-1};
      const peerEntries=(Array.isArray(diffPeers)?diffPeers:[]).map(peerVersion=>({version:peerVersion,skill:SB.skills[item?.id+'|'+peerVersion]})).filter(entry=>entry.skill&&!entry.skill.missing);
      const currentRank=versionRank(version),lower=peerEntries.filter(entry=>versionRank(entry.version)<currentRank).sort((a,b)=>versionRank(b.version)-versionRank(a.version)),upper=peerEntries.filter(entry=>versionRank(entry.version)>currentRank).sort((a,b)=>versionRank(a.version)-versionRank(b.version));
      const peerEntry=lower[0]||upper[0]||peerEntries[0],relation=peerEntry&&currentRank>=versionRank(peerEntry.version)?'newer':'older';
      const artifactReady=Boolean(skill&&!skill.missing&&skill.skillMd&&skill.instructionMd),highlight=artifactReady&&Boolean(peerEntry);
      const source=skill?.source||'';
      const legend='<small class="skill-diff-badge">Diff · <i class="modified">修改</i><i class="added">新增</i><i class="deleted">删除</i></small>';
      const badge=highlight?legend:artifactReady
        ?'<small>来源：'+escapeHTML(source)+'</small>'
        :'<small class="artifact-missing">generation_runs 未找到该版本的精确 Skill 产物</small>';
      const skillText=artifactReady?skill.skillMd:'# SKILL.md 缺失\n\ngeneration_runs 中没有与当前实验和版本精确对应的 SKILL.md。';
      const instructionText=artifactReady?skill.instructionMd:'# instruction.md 缺失\n\ngeneration_runs 中没有与当前实验和版本精确对应的 references/instructions.md。';
      const peerSkill=peerEntry?.skill;
      return '<div class="skill-doc-stack"><section class="skill-doc-box"><header class="skill-doc-box-head" data-skill-doc-toggle role="button" tabindex="0" aria-expanded="true"><h5>SKILL.md</h5>'+badge+'</header><div class="inline-document">'+markdownDiff(skillText,peerSkill?.skillMd,'skill',relation)+'</div></section><section class="skill-doc-box"><header class="skill-doc-box-head" data-skill-doc-toggle role="button" tabindex="0" aria-expanded="true"><h5>instruction.md</h5>'+(highlight?legend:'<small>'+(artifactReady?'来源：'+escapeHTML(source)+'/references/instructions.md':'不使用 state.json 或相似文段替代')+'</small>')+'</header><div class="inline-document">'+markdownDiff(instructionText,peerSkill?.instructionMd,'instruction',relation)+'</div></section></div>';
    };
    tracePanelHTML=function(experiment,version,caseIndex){
      const rec=record(experiment,version,caseIndex),trace=rec?.trace||{};
      const turns=Array.isArray(trace.conversation)?trace.conversation:[];
      const metrics=trace.metrics||{},usage=metrics.usage||{},steps=Array.isArray(metrics.steps)?metrics.steps:[];
      const hasTrace=Boolean(trace.source&&turns.length);
      const token=value=>Number(value||0).toLocaleString('zh-CN');
      const kindMeta={
        tool:{label:'Tool use',icon:'⌘'},thinking:{label:'深度思考',icon:'◌'},
        subagent:{label:'Sub-agent',icon:'◎'}
      };
      const detailBlock=(label,value)=>value?'<section class="trace-process-detail-block"><h5>'+label+'</h5><pre>'+escapeHTML(value)+'</pre></section>':'';
      const processHTML=(process,index)=>{
        const meta=kindMeta[process.kind]||kindMeta.tool,detailData=process.detail||{};
        const detailHTML=detailBlock('过程记录',detailData.text)+detailBlock('Input',detailData.input)+detailBlock('Result',detailData.result);
        return '<details class="trace-process trace-process-'+escapeHTML(process.kind||'tool')+'"><summary><span class="trace-process-icon">'+meta.icon+'</span><span class="trace-process-copy"><b>'+meta.label+(process.name&&process.name!==meta.label?' · '+escapeHTML(process.name):'')+'</b><small>'+escapeHTML(process.summary||process.name||'已记录执行过程')+'</small></span><span class="trace-process-meta">'+(process.durationMs?traceDuration(process.durationMs)+' · ':'')+escapeHTML(process.status||'recorded')+'</span><i class="trace-process-chevron">⌄</i></summary><div class="trace-process-level3"><div class="trace-process-level-label">第三级 · 执行细节</div>'+(detailHTML||'<div class="audit-note">该执行过程没有保存进一步细节。</div>')+'</div></details>';
      };
      const compactMessage=content=>{
        const value=String(content||'');
        if(value.length<=1400)return '<div class="trace-message-content inline-document">'+markdown(value)+'</div>';
        return '<div class="trace-message-content trace-message-preview inline-document">'+markdown(value.slice(0,700)+'…')+'</div><details class="trace-long-message"><summary>展开完整输入 · '+token(value.length)+' 字符</summary><div class="inline-document">'+markdown(value)+'</div></details>';
      };
      const turnHTML=turn=>{
        const role=turn.role==='user'?'user':'agent';
        if(role==='user')return '<article class="trace-turn trace-turn-user"><div class="trace-turn-role">USER · Round '+escapeHTML(turn.round||'—')+'</div><div class="trace-user-bubble">'+compactMessage(turn.content)+'</div></article>';
        const processes=Array.isArray(turn.processes)?turn.processes:[];
        const execution=processes.length?'<details class="trace-execution-group"><summary><span class="trace-execution-label">已处理 '+traceDuration(turn.durationMs)+'</span><span>'+processes.length+' 个执行过程</span><i>⌄</i></summary><div class="trace-process-list"><div class="trace-process-level-label">第二级 · 执行过程</div>'+processes.map(processHTML).join('')+'</div></details>':'<div class="trace-execution-empty">未保存工具、思考或 Sub-agent 过程</div>';
        return '<article class="trace-turn trace-turn-agent"><div class="trace-turn-role">AGENT · '+escapeHTML(trace.model||'model 未记录')+' · Round '+escapeHTML(turn.round||'—')+'</div>'+execution+'<div class="trace-agent-answer inline-document">'+markdown(turn.content||'该轮未保存最终回复。')+'</div></article>';
      };
      const metricCards='<div class="trace-metric-grid">'+
        '<div><small>总耗时</small><b>'+traceDuration(metrics.durationMs||trace.durationMs)+'</b></div>'+
        '<div><small>总 Token</small><b>'+token(usage.total_tokens)+'</b></div>'+
        '<div><small>输入 Token</small><b>'+token(usage.input_tokens)+'</b></div>'+
        '<div><small>输出 Token</small><b>'+token(usage.output_tokens)+'</b></div>'+
      '</div>';
      const stepRows=steps.length?'<details class="trace-step-metrics"><summary>运行指标 · '+steps.length+' 步</summary><div class="trace-step-list">'+steps.map(step=>
        '<div><b>Step '+escapeHTML(step.step)+'</b><span>总耗时 '+traceDuration(step.durationMs)+'</span><span>API '+traceDuration(step.apiDurationMs)+'</span><span>Token '+token(step.usage?.total_tokens)+'</span></div>'
      ).join('')+'</div></details>':'';
      const flow=hasTrace?turns.map(turnHTML).join(''):'<div class="trace-empty"><b>生成链路缺失</b><p>generation_runs 中没有可建立三级关系的 request、result 与 events/operations；未使用其他文件替代。</p></div>';
      return '<aside class="trace-panel trace-panel-codex"><div class="trace-panel-head"><div><b>报告生成链路</b><small class="trace-level-note">第一级：User / Agent 对话 · 来源：'+escapeHTML(trace.source||'conversation.md 未找到')+'</small></div><span>'+escapeHTML(trace.status||'missing')+' · '+traceDuration(metrics.durationMs||trace.durationMs)+'</span></div>'+
        (hasTrace?metricCards+stepRows:'')+'<div class="trace-conversation">'+flow+'</div></aside>';
    };
    const tracePanelWithRuntimeHeader=tracePanelHTML;
    const removeTraceHeaderElement=(html,startTag,endTag,fromIndex=0)=>{
      const start=html.indexOf(startTag,fromIndex);
      if(start<0)return html;
      const end=html.indexOf(endTag,start+startTag.length);
      return end<0?html:html.slice(0,start)+html.slice(end+endTag.length);
    };
    tracePanelHTML=function(...args){
      let html=tracePanelWithRuntimeHeader(...args);
      html=removeTraceHeaderElement(html,'<small class="trace-level-note">','</small>');
      const headerStart=html.indexOf('<div class="trace-panel-head">');
      if(headerStart>=0)html=removeTraceHeaderElement(html,'<span>','</span>',headerStart);
      return html;
    };
    compareDataPackage=function(){return ''};
  };
})();
