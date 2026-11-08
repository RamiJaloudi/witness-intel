let analysisMode='full',fullAnalysisActive=false,fullStatusLoading=false,lastFullStatus=null;
function updateAnalysisControls(){
 $('analyze').textContent=analysisMode==='full'?'Analyze full corpus':'Analyze next batch';
 $('analysisModeSummary').hidden=analysisMode==='full';
 $('analysisModeSummary').textContent='Manual mode: run one batch at a time. Change analysis scope in Settings.';
 $('manualAnalysisControls').classList.toggle('hidden',analysisMode==='full');
 $('autoWorkProduct').parentElement.querySelector('span').textContent=analysisMode==='full'?'Draft witness profiles and questions after analysis':'Automatically draft complete work product for witnesses identified in each batch';
 if(fullAnalysisActive)$('analyze').disabled=true;
}
async function startFullAnalysis(){
 const workspaceId=mid(),batchSize=Number($('analysisBatchSize').value);if(!Number.isInteger(batchSize)||batchSize<1||batchSize>500)throw Error('Choose 1–500 passages per internal batch.');
 const names=[...new Set(state.outputs.filter(o=>['witness','event','relationship'].includes(o.kind)).flatMap(o=>[o.payload.person,o.payload.other_person]).filter(Boolean))],questionSettings={};for(const name of names)questionSettings[name]=questionSettingsFor(name);
 const result=await api(`/matters/${workspaceId}/full-analysis/start`,{provider:$('provider').value,batch_size:batchSize,draft_work_product:$('autoWorkProduct').checked,question_settings:questionSettings});
 fullAnalysisActive=true;analysisInProgress=true;msg('Full-corpus analysis started. Progress is saved on the server; you may leave this page.');await loadFullAnalysisStatus();renderWorkflowStatus();updateAnalysisControls();return result
}
async function loadFullAnalysisStatus(){
 if(fullStatusLoading||!$('matter').value)return;fullStatusLoading=true;const workspaceId=$('matter').value;
 try{const r=await api(`/matters/${workspaceId}/full-analysis/status`);if($('matter').value!==workspaceId)return;const previous=lastFullStatus;lastFullStatus={...r,workspaceId};fullAnalysisActive=['running','paused'].includes(r.status);analysisInProgress=analysisRequestActive||fullAnalysisActive||state.runs.some(run=>run.status==='running'&&run.purpose==='extraction');
 const box=$('fullAnalysisStatus');box.replaceChildren();$('analysisStatus').classList.toggle('hidden',analysisMode==='full');
 box.classList.toggle('hidden',r.status==='not_started');
 if(r.status==='not_started'){box.append(el('strong','Full-corpus analysis is ready.'),el('p','Analyze full corpus processes all included source passages, then drafts each witness once if enabled.','hint'));}
 else{const phase=r.status==='running'?(r.cancel_requested?'cancel requested':r.pause_requested?'pause requested':r.phase):r.phase;box.append(el('strong',`Full corpus: ${r.status}${phase&&phase!==r.status?" · "+phase:""}`),el('p',`${r.provider} · ${r.model} · ${r.processed_passages}/${r.total_passages} passages · ${r.completed_documents}/${r.total_documents} documents complete`,'hint'));const bar=el('progress');bar.max=r.total_passages||1;bar.value=r.processed_passages;bar.style.width='100%';bar.setAttribute('aria-label','Full corpus progress');box.append(bar);
 if(r.draft_work_product&&r.processed_passages<r.total_passages)box.append(el('p','Witness profiles and questions begin after extraction finishes.','hint'));
 else if(r.draft_work_product)box.append(el('p',`Witness work products completed: ${r.witnesses_completed}/${r.witness_total||'pending extraction'}. ${r.current_witness?'Current witness: '+r.current_witness:''}`,'hint'));
 if(r.error)box.append(el('p',r.error,'warning'));if(r.status==='failed')box.append(el('p',`${r.processed_passages} completed passages are saved. Resume continues with the remaining ${Math.max(0,r.total_passages-r.processed_passages)} passages using this run's recorded provider and model. Rejected responses are not counted as processed.`,'hint'));if(r.next_retry_at&&r.phase==='retry_wait')box.append(el('p',`Automatic retry ${r.retry_count}/3 scheduled for ${new Date(r.next_retry_at).toLocaleString()}. Saved progress is retained.`,'hint'));
 if(r.status==='completed'&&r.processed_passages<r.total_passages)box.append(el('p','Additional or changed source material remains. Start another full-corpus run to include it.','warning'));
 const actions=el('div',undefined,'bar');for(const [label,action] of (r.status==='running'?[['Pause after current work','pause'],['Cancel full run','cancel']]:['paused','failed'].includes(r.status)?[['Resume full corpus','resume'],['Cancel full run','cancel']]:[])){const button=el('button',label,'secondary');button.disabled=r.status==='running'&&(r.cancel_requested||(r.pause_requested&&action==='pause'));button.onclick=()=>busy(async()=>{const result=await api(`/matters/${workspaceId}/full-analysis/control`,{action});msg(result.message);await loadFullAnalysisStatus()});actions.append(button)}const refreshButton=el('button','Refresh witness results','secondary');refreshButton.onclick=()=>busy(()=>refresh());actions.append(refreshButton);box.append(actions,el('p','Pause/cancel takes effect at the next passage or witness boundary. Full-corpus coverage means every included passage was processed; it does not guarantee that every fact or witness was found.','hint'));}
 renderWorkflowStatus();updateAnalysisControls();
 if(previous?.workspaceId===workspaceId&&previous.status==='running'&&['completed','failed','paused','cancelled'].includes(r.status))await refresh();
 }catch(error){if($('matter').value===workspaceId)$('fullAnalysisStatus').textContent=error.message}
 finally{fullStatusLoading=false}
}
$('analysisMode').onchange=()=>busy(async()=>{await api('/settings/analysis-mode',{mode:$('analysisMode').value});await refreshSettings();await loadFullAnalysisStatus();msg('Analysis scope saved. Active runs keep their current scope.')});
setInterval(()=>{if($('matter').value&&(fullAnalysisActive||!document.querySelector('section[data-tab="Witnesses"]').classList.contains('hidden')))void loadFullAnalysisStatus()},3000);
