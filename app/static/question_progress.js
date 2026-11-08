const observedQuestionRuns=new Set(),shownQuestionFailures=new Set();
let questionProgressPolling=false;
function questionRunName(run){return (run.manifest||[]).find(e=>e.kind==='question_request')?.witness||'Witness';}
function questionProgressBar(run){
 const progress=(run.manifest||[]).find(e=>e.kind==='question_progress')||{},box=el('div'),bar=el('progress');
 bar.style.width='100%';bar.setAttribute('aria-label','Question generation activity');
 if(run.status!=='running'){bar.max=1;bar.value=1}
 box.append(el('p',`${progress.phase||'Generation in progress'}${progress.batch?' · batch '+progress.batch:''}`),bar);
 if(progress.requested){const count=el('progress');count.max=progress.requested;count.value=progress.accepted||0;count.style.width='100%';count.setAttribute('aria-label','Verified questions toward requested target');box.append(el('p',`${progress.accepted||0} questions verified so far (validated batches saved as incomplete drafts) · target ${progress.requested}. The target is not guaranteed.`,'hint'),count)}
 const started=new Date(run.created_at).getTime();box.append(el('p',`Elapsed: ${Math.max(0,Math.floor((Date.now()-started)/60000))} min · ${run.provider} · ${run.model||'configured model'}. Model calls may take several minutes; activity does not estimate time remaining.`,'hint'));return box;
}
function renderQuestionProgress(){
 const box=$('questionGenerationProgress');if(!box)return;box.replaceChildren();
 const runs=state.runs.filter(r=>r.purpose==='questions'),running=runs.filter(r=>r.status==='running');box.classList.toggle('hidden',!running.length);
 for(const run of running){observedQuestionRuns.add(run.id);box.append(el('h3',`Generating questions for ${questionRunName(run)}`),questionProgressBar(run));}
 for(const run of runs)if(['failed','partial','interrupted'].includes(run.status)&&observedQuestionRuns.has(run.id)&&!shownQuestionFailures.has(run.id))showQuestionFailure(run);
}
function showQuestionFailure(run,message,witness){
 if(!run){run=state.runs.filter(r=>r.purpose==='questions'&&questionRunName(r)===witness&&['failed','partial','interrupted'].includes(r.status)).sort((a,b)=>b.created_at.localeCompare(a.created_at))[0]}
 if(run)shownQuestionFailures.add(run.id);
 let dialog=$('questionFailureDialog');if(!dialog){dialog=el('dialog');dialog.id='questionFailureDialog';dialog.className='source-dialog';document.body.append(dialog)}
 const error=(run?.manifest||[]).find(e=>e.kind==='question_error')||{},progress=(run?.manifest||[]).find(e=>e.kind==='question_progress')||{};
 dialog.replaceChildren(el('h3',`Question generation failed: ${run?questionRunName(run):witness||'Witness'}`),el('p',run?.error||message||'The request did not complete.','warning'));
 const details=el('dl');for(const [label,value] of [['Exception',error.exception||'GenerationError'],['Error code',error.code||'Unavailable for this older run'],['HTTP status',error.http_status||'See error message'],['Run ID',run?.id||'No run was created'],['Provider / model',run?`${run.provider} / ${run.model}`:'See selected provider'],['Last stage',progress.phase||'Not recorded'],['Verified questions before failure',progress.accepted??'Not recorded'],['Started',run?new Date(run.created_at).toLocaleString():'Not recorded']]){details.append(el('dt',label),el('dd',String(value)))}
 dialog.append(details,el('p','Existing outlines and validated draft batches are retained. Review incomplete batches in Review, or retry the saved request from Performance. Check provider configuration in Settings. Details omit credentials, document text and raw provider payloads.','hint'));
 const close=el('button','Close');close.onclick=()=>dialog.close();const settings=el('button','Open Settings','secondary');settings.onclick=()=>{dialog.close();tab('Settings')};dialog.append(close,settings);if(!dialog.open)dialog.showModal();
}
setInterval(async()=>{if(questionProgressPolling||!$('matter').value)return;const workspaceId=$('matter').value;questionProgressPolling=true;try{const runs=await api(`/matters/${workspaceId}/question-progress`);if($('matter').value!==workspaceId)return;state.runs=[...state.runs.filter(r=>r.purpose!=='questions'),...runs];renderQuestionProgress();if(runs.some(r=>r.status!=='running'&&observedQuestionRuns.has(r.id)&&!shownQuestionFailures.has('complete:'+r.id))){for(const r of runs.filter(r=>r.status!=='running'))shownQuestionFailures.add('complete:'+r.id);await refresh()}}catch{}finally{questionProgressPolling=false}},3000);
