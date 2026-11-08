/* Layout-only enhancements. Existing controls and their event handlers are moved, never cloned. */
const witnessCardOpen=new Map();
const witnessIcons={Review:'<path d="M5 3h14v18H5zM8 8l2 2 4-4m-6 9h8"/>',Case:'<path d="M3 7h18v14H3zM8 7V3h8v4m-4 4v6m-3-3h6"/>',Quality:'<path d="m12 3 3 6 6 1-4 5 1 6-6-3-6 3 1-6-4-5 6-1z"/>',Backups:'<path d="M4 3h14l3 3v15H3V3zm3 0v6h10V3M7 21v-8h10v8"/>',Performance:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',Import:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',Privacy:'<path d="m12 3 8 4v5c0 5-8 9-8 9s-8-4-8-9V7z"/><path d="m8 12 3 3 5-6"/>',Witnesses:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m3 10v-3a6 6 0 0 0-2-4"/>',Audit:'<path d="M5 3h10l4 4v14H5zM9 12h6m-6 4h6M14 3v5h5"/>',Settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>'};
for(const button of $('nav').children){const target=button.textContent;button.dataset.navTarget=target;const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.classList.add('nav-icon');icon.setAttribute('aria-hidden','true');icon.innerHTML=witnessIcons[target]||'';button.prepend(icon)}

function panel(title,nodes,{open=false,key=''}={}){
 const details=el('details',undefined,'workspace-panel'),summary=el('summary',title),body=el('div',undefined,'panel-content');
 details.open=open;if(key)details.dataset.layoutPanel=key;for(const node of nodes)body.append(node);details.append(summary,body);return details;
}
function sectionizeWitnessBody(body,name){
 const nodes=[...body.childNodes];let destination=body;
 for(const node of nodes){
  if(node.nodeType===1&&node.tagName==='H4'){
   const key=JSON.stringify([state.matter_id||$('matter').value,name,node.textContent]);
   const detail=panel(node.textContent,[],{open:witnessPanelState.has(key)?witnessPanelState.get(key):node.textContent==='Profile summary'});
   detail.dataset.witnessPanel=key;detail.dataset.sectionTitle=node.textContent;detail.ontoggle=()=>{if(detail.isConnected)witnessPanelState.set(key,detail.open)};
   destination=detail.querySelector('.panel-content');body.append(detail);node.remove();
  }else destination.append(node);
 }
}
function openWitnessQuestions(name){
 witnessCardOpen.set(JSON.stringify([state.matter_id||$('matter').value,name]),true);
 witnessPanelState.set(JSON.stringify([state.matter_id||$('matter').value,name,'Questions']),true);
}
function initializeCompactLayout(){
 const tools=el('details',undefined,'sidebar-workspace-tools');tools.append(el('summary','Workspace Tools'));const toolButtons=el('div');for(const id of ['deleteWorkspace','manageWorkspaces','workspaceTrash'])toolButtons.append($(id));tools.append(toolButtons);document.querySelector('.sidebar-foot').before(tools);
 const workflow=$('workflowStatus'),readiness=panel('Workspace Readiness',[]);workflow.before(readiness);readiness.querySelector('.panel-content').append(workflow);
 const readinessStatus=el('span','Review Import And Analysis Readiness','panel-status');readiness.querySelector('summary').append(readinessStatus);
 new MutationObserver(()=>{readinessStatus.textContent=workflow.querySelector('h3')?.textContent||'Workspace Readiness'}).observe(workflow,{childList:true,subtree:true,characterData:true});
 const settings=document.querySelector('section[data-tab="Settings"]'),content=el('div',undefined,'settings-grid');
 let current=null;
 for(const node of [...settings.children]){
  if(node.tagName==='H2')continue;
  if(node.classList.contains('appearance-panel')){node.querySelector('h3')?.remove();content.append(panel('Appearance And Color Palettes',[node]));current=null;continue}
  if(node.classList.contains('provider-settings')){const heading=node.querySelector('h3'),title=heading.textContent;heading.remove();content.append(panel(title,[node]));current=null;continue}
  if(node.tagName==='H3'){
   const title=node.textContent;current=panel(title,[],{open:title.includes('Ollama')});content.append(current);node.remove();continue;
  }
  if(node.id==='settings'){content.append(panel('Technical Details',[node]));current=null;continue}
  if(node.classList.contains('warning')){content.append(node);current=null;continue}
  if(!current){current=panel('Analysis And Provider Selection',[]);content.append(current)}
  current.querySelector('.panel-content').append(node);
 }
 settings.append(content);
 const witnessSection=document.querySelector('section[data-tab="Witnesses"]'),metrics=el('div',undefined,'dashboard-metrics');metrics.id='workspaceMetrics';
 witnessSection.insertBefore(metrics,$('fullAnalysisStatus'));
 const analysisNodes=[];let node=$('fullAnalysisStatus');while(node){const next=node.nextElementSibling;analysisNodes.push(node);if(node.id==='coverage')break;node=next}
 const analysis=panel('Analysis And Generation',analysisNodes,{key:'analysis'});analysis.id='analysisPanel';analysis.querySelector('summary').append(el('span','Saved Progress And Run Controls','panel-status'));metrics.after(analysis);
 const imports=document.querySelector('section[data-tab="Import"]');const importNodes=[];node=$('folder').closest('label');
 while(node){const next=node.nextElementSibling;if(node.classList.contains('grid'))break;importNodes.push(node);node=next}
 if(importNodes.length){const anchor=importNodes[0],details=panel('Add Evidence',[]);anchor.before(details);details.querySelector('.panel-content').append(...importNodes)}
 const bulk=$('bulkPrivacyPanel');bulk.before(panel('Bulk Privacy Review',[]));bulk.previousElementSibling.querySelector('.panel-content').append(bulk);
 const runs=$('runs');runs.before(panel('Processing History',[]));runs.previousElementSibling.querySelector('.panel-content').append(runs);
}
function refreshDashboard(){
 const outputs=state.outputs||[],findings=outputs.filter(o=>['witness','event','relationship'].includes(o.kind)),names=new Set(findings.flatMap(o=>[o.payload.person,o.payload.other_person]).filter(Boolean));
 const cards=[['Evidence Documents',state.documents.length,'Saved In This Workspace'],['Witness Candidates',names.size,'Source Linked Identities'],['Approved Findings',findings.filter(o=>o.review_status==='approved').length,'Attorney Reviewed'],['Drafted Questions',outputs.filter(o=>o.kind==='questions').reduce((n,o)=>n+(o.payload.questions||[]).length,0),'Across All Saved Outlines']];
 $('workspaceMetrics').replaceChildren(...cards.map(([label,value,note])=>{const card=el('div',undefined,'metric-card');card.append(el('span',label,'metric-label'),el('strong',value.toLocaleString(),'metric-value'),el('small',note,'metric-note'));return card}));
 applyWitnessSearch();if(typeof refreshExportSelection==='function')refreshExportSelection();
}
function applyWitnessSearch(){
 const query=($('witnessSearch')?.value||'').trim().toLocaleLowerCase();
 for(const card of $('witnesses').querySelectorAll('[data-witness-card]'))card.hidden=Boolean(query&&!card.dataset.witness.toLocaleLowerCase().includes(query));
 for(const row of $('witnessIndex').querySelectorAll('[data-witness-name]'))row.hidden=Boolean(query&&!row.dataset.witnessName.toLocaleLowerCase().includes(query));
 const cards=[...$('witnesses').querySelectorAll('[data-witness-card]')];$('witnessSearchEmpty').hidden=!cards.length||cards.some(card=>!card.hidden);
}
$('witnessSearch').oninput=()=>{witnessViewPage=0;$('profilePerson').value='';renderProfile()};
$('witnessSort').onchange=()=>{witnessViewPage=0;$('profilePerson').value='';renderProfile()};
function refreshWitnessPager(){
 const {total,page,count,selected}=witnessPagingState;
 $('witnessPaging').hidden=Boolean(selected)||!total;
 $('witnessPageRange').textContent=`Showing ${total?page*12+1:0}–${page*12+count} Of ${total} Witnesses`;
 $('witnessPrevious').disabled=page<=0;
 $('witnessNext').disabled=(page+1)*12>=total;
}
$('witnessPrevious').onclick=()=>{witnessViewPage=Math.max(0,witnessViewPage-1);renderProfile()};
$('witnessNext').onclick=()=>{witnessViewPage++;renderProfile()};
$('expandWitnesses').onclick=()=>{for(const card of $('witnesses').querySelectorAll('[data-witness-card]'))if(!card.hidden)card.open=true};
$('collapseWitnesses').onclick=()=>{for(const card of $('witnesses').querySelectorAll('[data-witness-card]'))card.open=false};
initializeCompactLayout();

// Normalize authored action labels, preserving source names, citations and swatches.
function normalizeActionLabels(root=document){
 const buttons=root.nodeType===1&&['BUTTON','SUMMARY'].includes(root.tagName)?[root]:[...root.querySelectorAll('button,summary')];
 for(const button of buttons){
  if(button.hasAttribute('data-preserve-case')||button.closest('#witnessIndex')||button.closest('.source-inline')||(button.tagName==='SUMMARY'&&button.closest('#sourceDialog'))||button.classList.contains('citation')||button.classList.contains('palette-choice'))continue;
  for(const node of button.childNodes)if(node.nodeType===3){const title=node.textContent.replace(/\b[a-z]/g,letter=>letter.toUpperCase());if(title!==node.textContent)node.textContent=title}
 }
}
let normalizePending=false;
new MutationObserver(()=>{if(!normalizePending){normalizePending=true;requestAnimationFrame(()=>{normalizePending=false;normalizeActionLabels()})}}).observe(document.body,{childList:true,subtree:true,characterData:true});
normalizeActionLabels();
