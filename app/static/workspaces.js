/* Workspace deletion is an audited, recoverable move to Trash. */
async function confirmWorkspaceDeletion(selected){
 const previews=await Promise.all(selected.map(m=>api(`/matters/${m.id}/deletion-preview`)));
 const dialog=$('sourceDialog'),body=$('sourceBody');$('sourceTitle').textContent=previews.length===1?'Delete workspace':'Delete selected workspaces';body.replaceChildren();
 body.append(el('p',`Move ${previews.length} workspace${previews.length===1?'':'s'} to Trash? They will disappear from the active dropdown. Records and stored copies are retained for restoration. Original source folders are unchanged.`));
 const {wrap,body:rows}=table(['Workspace','Documents','Passages','Outputs']);for(const p of previews){const row=el('tr');row.append(el('td',`${p.name} (${p.id.slice(0,8)})`),el('td',p.documents),el('td',p.passages),el('td',p.outputs));rows.append(row)}body.append(wrap);
 const expected=previews.length===1?previews[0].name:`DELETE ${previews.length}`,label=el('label'),input=el('input');label.append(el('span',`Type ${expected} to confirm`),input);body.append(label);const submit=el('button','Move to Trash');submit.disabled=true;input.oninput=()=>submit.disabled=input.value!==expected;submit.onclick=()=>busy(async()=>{
  if(input.value!==expected)throw Error('Confirmation does not match.');let deleted=0;
  try{for(const p of previews){await api(`/matters/${p.id}/delete`,{confirmation_name:p.name});deleted++}}
  catch(e){throw Error(`${deleted} workspace(s) moved to Trash. ${e.message}`)}
  finally{if(deleted){dialog.close();await loadMatters();tab('Witnesses')}}
  msg(`${deleted} workspace(s) moved to Trash. Use Trash to restore them.`);
 });body.append(submit);if(!dialog.open)dialog.showModal();
}
$('deleteWorkspace').onclick=()=>busy(async()=>{const id=mid();await confirmWorkspaceDeletion([{id}])});
$('workspaceTrash').onclick=()=>busy(showWorkspaceTrash);
$('manageWorkspaces').onclick=()=>busy(async()=>{const list=await api('/matters'),dialog=$('sourceDialog'),body=$('sourceBody');$('sourceTitle').textContent='Manage workspaces';body.replaceChildren(el('p','Select workspaces to delete. You will review counts and confirm before they move to Trash.','hint'));const choices=[],all=el('input');all.type='checkbox';const selectLabel=el('label');selectLabel.append(all,el('span','Select all listed workspaces'));body.append(selectLabel);const entries=el('div');entries.style.maxHeight='360px';entries.style.overflow='auto';for(const m of list){const label=el('label'),check=el('input');check.type='checkbox';label.append(check,el('span',`${m.name} · ${m.id.slice(0,8)}`));choices.push({check,m});entries.append(label)}body.append(entries);const review=el('button','Review selected deletions');review.disabled=true;function update(){const count=choices.filter(x=>x.check.checked).length;review.disabled=!count;review.textContent=`Review ${count} selected deletion${count===1?'':'s'}`}choices.forEach(x=>x.check.onchange=update);all.onchange=()=>{choices.forEach(x=>x.check.checked=all.checked);update()};review.onclick=()=>busy(()=>confirmWorkspaceDeletion(choices.filter(x=>x.check.checked).map(x=>x.m)));body.append(review);if(!dialog.open)dialog.showModal()});
