const addedProviders={openai:'OpenAI',anthropic:'Anthropic Claude'};
function refreshProviderSettings(settings){
 for(const [provider,name] of Object.entries(addedProviders)){
  const config=settings.providers?.[provider];
  if(!config)continue;
  // Preserve a model being typed while another setting is refreshed.
  if(document.activeElement!==$(provider+'Model'))$(provider+'Model').value=config.model;
  $(provider+'KeyStatus').textContent=config.key_configured?`${name} API key is configured. Paste a new key to replace it.`:`No ${name} API key configured.`;
 }
}
for(const [provider,name] of Object.entries(addedProviders)){
 $('save'+provider+'Key').onclick=()=>busy(async()=>{
  const input=$(provider+'Key'),key=input.value.trim();
  if(!key)throw Error(`Paste your ${name} API key first.`);
  try{
   await api(`/settings/providers/${provider}/key`,{api_key:key});
   await refreshSettings();msg(`${name} API key saved locally. No restart required.`);
  }finally{input.value=''}
 });
 $('save'+provider+'Model').onclick=()=>busy(async()=>{
  await api(`/settings/providers/${provider}/model`,{model:$(provider+'Model').value.trim()});
  await refreshSettings();msg(`${name} model saved for new runs.`);
 });
 $('load'+provider+'Models').onclick=()=>busy(async()=>{
  const result=await api(`/settings/providers/${provider}/models`);
  $(provider+'Models').replaceChildren(...result.models.map(model=>{const option=el('option',model);option.value=model;return option}));
  $(provider+'TestStatus').textContent=`${result.models.length} models loaded. Choose a model or enter its ID, save it, then test structured output. Catalog presence does not guarantee compatibility.`;
 });
 $('test'+provider).onclick=()=>busy(async()=>{
  const status=$(provider+'TestStatus');
  status.textContent='Testing the saved model with synthetic text; no workspace documents are sent.';
  try{
   const result=await api(`/settings/providers/${provider}/test`,{});
   status.textContent=`Connection and structured output succeeded using ${result.model}. No workspace documents were sent.`;
  }catch(error){status.textContent=error.message;throw error}
 });
}
