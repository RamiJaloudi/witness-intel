let bulkPrivacyWorkspace='',bulkPrivacySelected=new Set();
function pendingPrivacyDocuments(){
 const counts=new Map();for(const c of state.chunks){if(!counts.has(c.document_id))counts.set(c.document_id,{total:0,pending:0});const entry=counts.get(c.document_id);entry.total++;if(!c.approved)entry.pending++}
 return state.documents.filter(d=>!['failed','excluded'].includes(d.status)&&counts.get(d.id)?.pending).map(d=>({...d,...counts.get(d.id)}));
}
function renderBulkPrivacy(){
 const workspace=state.matter_id||$('matter').value;if(bulkPrivacyWorkspace!==workspace){bulkPrivacySelected.clear();bulkPrivacyWorkspace=workspace;$('bulkPrivacySearch').value=''}
 const all=pendingPrivacyDocuments(),ids=new Set(all.map(d=>d.id));bulkPrivacySelected=new Set([...bulkPrivacySelected].filter(id=>ids.has(id)));
 const query=$('bulkPrivacySearch').value.toLowerCase(),visible=all.filter(d=>d.name.toLowerCase().includes(query)),list=$('bulkPrivacyDocuments'),scrollTop=list.scrollTop;list.replaceChildren();
 if(!all.length)list.append(el('p','No pending documents with readable text.','empty-state'));
 for(const d of visible){const row=el('div',undefined,'bulk-review-entry'),label=el('label'),check=el('input');check.type='checkbox';check.checked=bulkPrivacySelected.has(d.id);label.append(check,el('span',`${d.name} · ${d.pending}/${d.total} passages pending`));check.onchange=()=>{if(check.checked)bulkPrivacySelected.add(d.id);else bulkPrivacySelected.delete(d.id);renderBulkPrivacy()};const review=el('button','Review','secondary');review.onclick=()=>{$('privacyDocument').value=d.id;privacyPage=0;void renderPrivacyQueue()};row.append(label,review);list.append(row)}
 list.scrollTop=scrollTop;const selectedVisible=visible.filter(d=>bulkPrivacySelected.has(d.id)).length,allBox=$('bulkPrivacySelectAll');allBox.checked=visible.length>0&&selectedVisible===visible.length;allBox.indeterminate=selectedVisible>0&&selectedVisible<visible.length;
 $('bulkPrivacySelectAllText').textContent=`Select all ${visible.length} matching pending documents`;
 $('bulkPrivacyCount').textContent=`${bulkPrivacySelected.size} documents selected · ${all.filter(d=>bulkPrivacySelected.has(d.id)).reduce((n,d)=>n+d.total,0)} passages in selection · ${all.length} pending documents overall`;
 $('bulkApproveDocuments').disabled=!bulkPrivacySelected.size||state.runs.some(r=>r.status==='running');
}
$('bulkPrivacySearch').oninput=renderBulkPrivacy;
$('bulkPrivacySelectAll').onchange=()=>{const selected=$('bulkPrivacySelectAll').checked,query=$('bulkPrivacySearch').value.toLowerCase();for(const d of pendingPrivacyDocuments().filter(d=>d.name.toLowerCase().includes(query))){if(selected)bulkPrivacySelected.add(d.id);else bulkPrivacySelected.delete(d.id)}renderBulkPrivacy()};
$('bulkApproveDocuments').onclick=()=>busy(async()=>{
 const workspace=mid(),ids=[...bulkPrivacySelected],preview=await api(`/matters/${workspace}/privacy/bulk-preview`,{document_ids:ids});if(mid()!==workspace)throw Error('Workspace changed; review the selection again.');
 const dialog=$('sourceDialog'),body=$('sourceBody');$('sourceTitle').textContent='Approve selected documents';body.replaceChildren(el('p',`${preview.document_count} documents · ${preview.passage_count} passages · ${preview.pending_passages} currently pending`));
 const names=el('div',undefined,'selection-list');for(const d of preview.documents)names.append(el('p',`${d.name} · ${d.passages} passages`,'source-meta'));body.append(names);
 const label=el('label'),check=el('input');check.type='checkbox';label.append(check,el('span',`I have reviewed all passages of these ${preview.document_count} documents for PII/PHI and explicitly approve any retained real names for AI use.`));body.append(label,el('p','This changes approval status only and preserves completed analysis checkpoints. It does not edit or redact source text, approve generated findings, or start an AI analysis.','hint'));
 const status=el('p'),approve=el('button',`Approve ${preview.document_count} documents`);approve.disabled=true;check.onchange=()=>approve.disabled=!check.checked;
 approve.onclick=()=>busy(async()=>{if(!check.checked||mid()!==workspace)throw Error('Confirm the selected workspace and review acknowledgement first.');status.textContent='Saving bulk approval…';try{const result=await api(`/matters/${workspace}/privacy/bulk-approve`,{document_ids:ids,names_reviewed:true,expected_snapshots:preview.snapshots});dialog.close();bulkPrivacySelected.clear();await refresh();msg(`${result.approved_documents} documents and ${result.approved_passages} pending passages approved. No AI request was made.`)}catch(e){status.textContent=e.message;throw e}});body.append(status,approve);if(!dialog.open)dialog.showModal();
});
