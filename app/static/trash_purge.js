async function showWorkspaceTrash(){
 const [list,pending]=await Promise.all([api('/workspaces/trash'),api('/workspaces/trash/pending-purges')]);
 const dialog=$('sourceDialog'),body=$('sourceBody');$('sourceTitle').textContent='Workspace Trash';body.replaceChildren(el('p','Restore a workspace, permanently delete one, or select several to purge. Permanent purge cannot be undone and removes managed database records and stored copies.','hint'));
 if(pending.length){const box=el('div',undefined,'warning');box.append(el('h4','Pending purge recovery / file cleanup'));for(const operation of pending){const row=el('div');row.append(el('p',`Operation ${operation.operation_id.slice(0,8)} · ${operation.workspace_ids.length} workspace(s) · ${operation.state}`));const resolve=el('button','Resolve pending operation','secondary');resolve.onclick=()=>confirmPendingPurge(operation);row.append(resolve);box.append(row)}body.append(box)}
 if(!list.length)body.append(el('p','Trash is empty.','empty-state'));
 else{
  const all=el('input');all.type='checkbox';const label=el('label');label.append(all,el('span',`Select all ${list.length} trashed workspaces`));body.append(label);
  const entries=el('div',undefined,'selection-list'),choices=[];
  for(const m of list){const row=el('article',undefined,'trash-entry'),select=el('input');select.type='checkbox';const name=el('label');name.append(select,el('span',`${m.name} · ${m.id.slice(0,8)}`));choices.push({select,m});const controls=el('div',undefined,'review-actions'),restore=el('button','Restore','secondary'),purge=el('button','Permanently delete','danger-secondary');
   restore.onclick=()=>busy(async()=>{await api(`/matters/${m.id}/restore`,{});dialog.close();await loadMatters(m.id);msg('Workspace restored.');tab('Witnesses')});
   purge.onclick=()=>busy(()=>confirmPermanentPurge([m.id]));controls.append(restore,purge);row.append(name,controls);entries.append(row);
  }
  body.append(entries);const buttons=el('div',undefined,'review-actions'),selected=el('button','Purge selected (0)','danger'),empty=el('button',`Purge all Trash (${list.length})`,'danger-secondary');selected.disabled=true;
  function update(){const count=choices.filter(c=>c.select.checked).length;selected.disabled=!count;selected.textContent=`Purge selected (${count})`;all.checked=count===choices.length;all.indeterminate=count>0&&count<choices.length}
  choices.forEach(c=>c.select.onchange=update);all.onchange=()=>{choices.forEach(c=>c.select.checked=all.checked);update()};
  selected.onclick=()=>busy(()=>confirmPermanentPurge(choices.filter(c=>c.select.checked).map(c=>c.m.id)));empty.onclick=()=>busy(()=>confirmPermanentPurge(list.map(m=>m.id)));buttons.append(selected,empty);body.append(buttons);
 }
 if(!dialog.open)dialog.showModal();
}
async function confirmPermanentPurge(ids){
 const preview=await api('/workspaces/trash/purge-preview',{workspace_ids:ids}),dialog=$('sourceDialog'),body=$('sourceBody');$('sourceTitle').textContent=ids.length===1?'Permanently delete workspace':`Purge ${ids.length} workspaces`;body.replaceChildren();
 body.append(el('p','This permanently removes the selected workspace records, documents, extracted passages, findings, questions, runs and workspace audit history. Stored copies are deleted. You cannot restore this action from Trash.','warning'));
 const c=preview.counts;body.append(el('p',`${c.workspaces} workspaces · ${c.documents} documents · ${c.passages} passages · ${c.outputs} outputs · ${c.runs} runs · ${c.audit_events} audit records · ${c.stored_files} stored files (${(c.stored_bytes/1048576).toFixed(1)} MiB)`));
 const names=el('div',undefined,'selection-list');for(const m of preview.workspaces)names.append(el('p',`${m.name} · ${m.id}`,'source-meta'));body.append(names,el('p','Original input folders, separately exported files and backups are outside this purge. A minimal operation receipt retains UUIDs, timestamps and counts only. This is file deletion, not forensic disk overwriting.','hint'));
 const label=el('label'),input=el('input');label.append(el('span',`Type ${preview.confirmation} to confirm permanent deletion`),input);const status=el('p',undefined,'hint'),submit=el('button','Permanently purge','danger');submit.disabled=true;input.oninput=()=>submit.disabled=input.value!==preview.confirmation;
 submit.onclick=()=>busy(async()=>{if(input.value!==preview.confirmation)throw Error('Confirmation does not match.');status.textContent='Purging selected workspace data…';try{const result=await api('/workspaces/trash/purge',{workspace_ids:ids,confirmation:input.value});dialog.close();await loadMatters();await showWorkspaceTrash();msg(result.completed?`${result.purged_workspaces} workspace(s) permanently purged.`:'Database records were removed, but file cleanup is pending. Use Resolve pending operation in Trash.')}catch(e){status.textContent=e.message;throw e}});
 body.append(label,status,submit);if(!dialog.open)dialog.showModal();
}
function confirmPendingPurge(operation){
 const dialog=$('sourceDialog'),body=$('sourceBody');$('sourceTitle').textContent='Resolve pending purge';body.replaceChildren(el('p',`Operation ${operation.operation_id}`,'source-meta'),el('p','If the database deletion was not committed, this restores staged files. If records were already removed, it permanently deletes the remaining staged copies. Original input folders are unchanged.','warning'));
 const label=el('label'),input=el('input');label.append(el('span','Type RESOLVE PURGE to confirm'),input);const submit=el('button','Resolve operation','danger-secondary'),status=el('p');submit.disabled=true;input.oninput=()=>submit.disabled=input.value!=='RESOLVE PURGE';submit.onclick=()=>busy(async()=>{try{const result=await api(`/workspaces/trash/pending-purges/${operation.operation_id}/resolve`,{confirmation:input.value});dialog.close();await loadMatters();await showWorkspaceTrash();msg(result.message||(result.completed?'Pending purge cleanup completed.':'Some files remain inaccessible; cleanup is still pending.'))}catch(e){status.textContent=e.message;throw e}});body.append(label,status,submit);if(!dialog.open)dialog.showModal();
}
