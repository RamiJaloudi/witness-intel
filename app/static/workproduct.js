let workProductBusy=false;
async function runWitnessProduct(name){
 const workspaceId=mid(),status=$('workProductStatus');status.textContent=`Drafting work product for ${name}…`;
 const poll=setInterval(async()=>{try{const r=await api(`/matters/${workspaceId}/work-product/status`);if(mid()===workspaceId)status.textContent=`${name}: ${r.status}; completed stages: ${r.stages.map(x=>x.stage).join(', ')||'starting'}. ${r.error||''}`}catch{}},3000);
 try{const settings=questionSettingsFor(name),request={witness:name,provider:$('provider').value,instructions:settings.topics,question_count:Number(settings.questionCount)};for(const key of ['alignment','proceeding','examination','style'])request[key]=settings[key];await api(`/matters/${workspaceId}/work-product`,request);status.textContent=`Draft work product for ${name} saved. Review sources, possible issues and questions before final use.`}
 finally{clearInterval(poll);await refresh()}
}
async function draftWitnessProduct(name){return busy(async()=>{if(workProductBusy)throw Error('A work product workflow is already running.');workProductBusy=true;$('matter').disabled=true;try{await runWitnessProduct(name)}finally{workProductBusy=false;$('matter').disabled=false}})}
function currentWitnessProduct(name){return state.outputs.filter(o=>o.kind==='work_product'&&o.review_status!=='rejected'&&o.payload.witness===name).sort((a,b)=>b.created_at.localeCompare(a.created_at))[0]}
function productSourcesCurrent(product){return !(product.payload.claim_manifest||[]).some(entry=>{const current=state.outputs.find(o=>o.id===entry.id);return !current||current.revision!==entry.revision||current.review_status==='rejected'})}
function renderNarrative(container,name,overview){
 const product=currentWitnessProduct(name);if(!product)return false;
 if(!productSourcesCurrent(product)){container.append(el('p','The saved narrative needs regeneration because its source findings changed. The current evidence digest is shown instead.','hint'));return false}
 const profile=product.payload.profile||[],words=profile.reduce((n,item)=>n+item.text.trim().split(/\s+/).length,0),ids=new Set(state.outputs.filter(o=>o.review_status!=='rejected'&&[o.payload.person,o.payload.other_person].includes(name)).map(o=>o.id));
 const valid=profile.length&&profile.every(item=>item.claim_ids?.length&&item.claim_ids.every(id=>ids.has(id))&&!/^[\s"“']*(I|we)\b/i.test(item.text))&&profile[0].text.includes(name);
 if(!valid||(overview?.limited&&words>60)){container.append(el('p','A suitable narrative profile has not yet been established. A concise description from the available evidence is shown below.','hint'));return false}
 container.append(el('p',product.review_status==='approved'?'Attorney-reviewed narrative.':'Draft narrative — attorney review required.','hint'));
 for(const item of profile){const paragraph=el('p',item.text.replace(/\s*\(claim_ids?:[^)]*\)/gi,''),'summary-line');for(const id of item.claim_ids)paragraph.append(document.createTextNode(' '),citation(id));container.append(paragraph)}
 return true;
}
function renderWitnessIssues(container,name){
 const product=currentWitnessProduct(name);if(!product||!productSourcesCurrent(product))return;
 if(product.payload.issues?.length){container.append(el('h4','Possible factual and legal issues'));for(const item of product.payload.issues){const paragraph=el('p',item.text.replace(/\s*\(claim_ids?:[^)]*\)/gi,''));paragraph.prepend(el('strong',item.category==='possible_issue'?'Possible issue: ':'Open question: '));for(const id of item.claim_ids||[])paragraph.append(document.createTextNode(' '),citation(id));container.append(paragraph)}}
 const info=el('details');info.append(el('summary','Profile generation and review details'));const run=state.runs.find(r=>r.id===product.run_id),c=product.payload.coverage||{};info.append(el('p',`Workflow: ${run?.status||'unknown'}. Research retrieved ${product.payload.retrieved_passages||0} passages. Retrieval is selective and does not establish full-corpus coverage. ${c.warning||''}`,'hint'),badge(product.review_status),claimReview(product));container.append(info)
}
$('indexCorpus').onclick=()=>busy(async()=>{try{const r=await api(`/matters/${mid()}/search/index`,{limit:100});msg(`Indexed ${r.indexed} masked passages locally; ${r.remaining} remain of ${r.total}.`)}finally{await refreshIndexStatus()}});
$('searchCorpus').onclick=()=>busy(async()=>{const r=await api(`/matters/${mid()}/search`,{query:$('corpusQuery').value}),box=$('corpusResults');box.replaceChildren(el('p',`${r.indexed_current}/${r.total_passages} passages currently indexed. ${r.warning}`,'hint'));for(const item of r.results){const document=state.documents.find(d=>d.id===item.document_id),details=el('details');details.append(el('summary',`${document?.name||item.document_id} · ${item.locator}`),el('pre',item.text));box.append(details)}});

let indexStatusLoading=false,indexStatusState=null,indexModeSaving=false;
function updateIndexButton(){const state=indexStatusState,button=$('indexCorpus');button.disabled=$('indexingMode').value!=='manual'||Boolean(state?.worker_running);button.title=$('indexingMode').value==='automatic'?'Automatic indexing is enabled; no repeated clicks are needed.':state?.worker_running?'Waiting for the current passage to finish.':'Index one batch manually.'}
async function refreshIndexStatus(){
 if(indexStatusLoading||!$('matter').value)return;indexStatusLoading=true;const workspaceId=$('matter').value;
 try{const result=await api(`/matters/${workspaceId}/search/index/status`);if($('matter').value!==workspaceId)return;indexStatusState=result;if(!indexModeSaving)$('indexingMode').value=result.mode;const box=$('indexingStatus');box.replaceChildren();const labels={queued:'Queued for automatic indexing',indexing:'Indexing locally',complete:'Index is current',manual:'Manual indexing selected',paused_analysis:'Paused while analysis is running',error:'Indexing needs attention'};box.append(el('p',`${labels[result.phase]||result.phase}: ${result.current} / ${result.total} passages current; ${result.remaining} remaining.`,'hint'));const progress=el('progress');progress.max=result.total||1;progress.value=result.current;progress.style.width='100%';progress.setAttribute('aria-label','Indexing progress');box.append(progress);if(result.phase==='manual')box.append(el('p','Automatic indexing is off. Use the manual batch button to continue.','hint'));else if(result.message)box.append(el('p',result.message,'hint'));if(result.phase==='error')box.append(el('p','Saved embeddings are retained. Automatic mode retries after a delay; check that Ollama and nomic-embed-text are available.','warning'));updateIndexButton()}
 catch(error){if($('matter').value===workspaceId)$('indexingStatus').textContent=error.message}
 finally{indexStatusLoading=false}
}
$('indexingMode').onchange=()=>busy(async()=>{indexModeSaving=true;try{const result=await api('/settings/indexing',{mode:$('indexingMode').value});msg(result.message);await refreshSettings()}finally{indexModeSaving=false;await refreshIndexStatus()}});
setInterval(()=>{if(!document.querySelector('section[data-tab="Settings"]').classList.contains('hidden'))void refreshIndexStatus()},5000);
