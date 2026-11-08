const witnessSelections=new Map();
const exportSectionNames={profile:'Profile Summary',issues:'Possible Issues',relationships:'Relationships',chronology:'Chronology',findings:'Supporting Findings',questions:'Questions',details:'Generation And Review Details'};
function selectedWitnesses(){const key=state.matter_id||$('matter').value;if(!witnessSelections.has(key))witnessSelections.set(key,new Set());return witnessSelections.get(key)}
function availableExportWitnesses(){return [...new Set(state.outputs.flatMap(o=>['witness','event','relationship'].includes(o.kind)?[o.payload.person,o.payload.other_person]:o.kind==='work_product'?[o.payload.witness]:o.kind==='questions'?[o.payload.settings?.witness]:[]).filter(Boolean))].sort()}
function setWitnessSelection(name,checked){const selection=selectedWitnesses();if(checked)selection.add(name);else selection.delete(name);refreshExportSelection()}
function refreshExportSelection(){
 const selection=selectedWitnesses(),names=availableExportWitnesses();for(const name of [...selection])if(!names.includes(name))selection.delete(name);
 $('exportSelectionCount').textContent=`${selection.size} Selected`;$('exportWitnessCsv').disabled=!selection.size;$('exportAllWitnesses').disabled=!names.length;
 for(const input of $('witnessIndex').querySelectorAll('input[data-select-witness]'))input.checked=selection.has(input.dataset.selectWitness);
}
$('selectAllWitnesses').onclick=()=>{for(const name of availableExportWitnesses())selectedWitnesses().add(name);refreshExportSelection()};
$('clearWitnessSelection').onclick=()=>{selectedWitnesses().clear();refreshExportSelection()};
function openWitnessExport(all=false){
 const names=availableExportWitnesses(),selection=selectedWitnesses();if(!names.length){msg('No saved witnesses are available to export.');return}
 const workspaceId=mid(),dialog=$('witnessExportDialog'),list=$('exportWitnessList');dialog.dataset.workspaceId=workspaceId;list.replaceChildren();
 for(const name of names){const label=el('label',undefined,'export-check-label'),input=el('input');input.type='checkbox';input.value=name;input.checked=all||selection.has(name);input.onchange=updateExportDialogCount;label.append(input,el('span',name));list.append(label)}
 for(const input of dialog.querySelectorAll('[data-export-section],[data-export-status]'))input.checked=true;
 $('exportWitnessFilter').value='';$('exportDownloadStatus').textContent='';updateExportDialogCount();dialog.showModal();
}
function updateExportDialogCount(){const inputs=[...$('exportWitnessList').querySelectorAll('input')];$('exportDialogCount').textContent=`${inputs.filter(input=>input.checked).length} Of ${inputs.length} Witnesses Selected`}
$('exportWitnessCsv').onclick=()=>openWitnessExport(false);
$('exportAllWitnesses').onclick=()=>openWitnessExport(true);
$('closeWitnessExport').onclick=()=>$('witnessExportDialog').close();
$('exportDialogSelectAll').onclick=()=>{for(const input of $('exportWitnessList').querySelectorAll('input'))input.checked=true;updateExportDialogCount()};
$('exportDialogClear').onclick=()=>{for(const input of $('exportWitnessList').querySelectorAll('input'))input.checked=false;updateExportDialogCount()};
$('exportWitnessFilter').oninput=()=>{const query=$('exportWitnessFilter').value.trim().toLocaleLowerCase();for(const label of $('exportWitnessList').children)label.hidden=!label.textContent.toLocaleLowerCase().includes(query)};
async function downloadWitnessExport(format){
 const dialog=$('witnessExportDialog'),workspaceId=dialog.dataset.workspaceId,status=$('exportDownloadStatus');
 const body={witnesses:[...$('exportWitnessList').querySelectorAll('input:checked')].map(input=>input.value),sections:[...dialog.querySelectorAll('[data-export-section]:checked')].map(input=>input.value),statuses:[...dialog.querySelectorAll('[data-export-status]:checked')].map(input=>input.value),format:format==='word'?$('exportWordLayout').value:'csv'};
 if(!body.witnesses.length||!body.sections.length||!body.statuses.length){status.textContent='Select At Least One Witness, Section And Review Status.';return}
 if(workspaceId!==mid()){status.textContent='The Workspace Changed. Close And Reopen Export.';return}
 const buttons=[...dialog.querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);status.textContent='Preparing Your Export…';
 try{
  const response=await fetch(`/api/matters/${encodeURIComponent(workspaceId)}/witnesses/export`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){const error=await response.json().catch(()=>({detail:'Export failed'}));throw Error(typeof error.detail==='string'?error.detail:'Unable To Export The Selected Witnesses.')}
  const blob=await response.blob(),url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=response.headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1]||'witness-export';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  status.textContent=`Downloaded ${response.headers.get('X-Export-Witnesses')} Witnesses And ${response.headers.get('X-Export-Items')} Saved Items.`;
 }catch(error){status.textContent=error.message}finally{buttons.forEach(button=>button.disabled=false)}
}
$('downloadWitnessCsv').onclick=()=>downloadWitnessExport('csv');
$('downloadWitnessWord').onclick=()=>downloadWitnessExport('word');
