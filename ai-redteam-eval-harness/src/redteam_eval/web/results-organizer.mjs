// Names and organization are separate from the measured job objects.
export function installResultsOrganizer({api,node,option,state,refreshJobs,openSkills,renderSkills,downloadSkillJson}) {
  const $=id=>document.getElementById(id);
  let metadata=null, selectedId=null, page=0, editGeneration=0, signature='';
  const empty=()=>({title:'',tags:[],note:'',favorite:false,archived:false});
  const details=id=>metadata?.records[id] || empty();
  const job=id=>state.jobs.find(item=>item.id===id);
  const feedback=text=>{$('organizer-feedback').textContent=text;};
  const eligible=item=>item?.action==='check_model' && ['complete','failed','cancelled'].includes(item.status) && item.result?.results?.length;
  const typeLabel=item=>({check_model:'Model skills',evaluate:'Security evaluation',replay:'Recorded replay',analyze:'Case explanation',prompt:'Prompt reply'}[item.action] || item.action);
  const label=item=>details(item.id).title || (item.action==='prompt' && item.request.title) || `${typeLabel(item)} · ${item.request.model} · ${item.id.slice(0,8)}`;
  function apply(data) {metadata=data;state.jobDetails=data.records;state.referenceJobId=data.reference_job_id;state.jobDetailsRevision=data.revision;signature='';renderList();if($('skills-dialog').open) renderSkills();}
  function matches() {
    const query=$('organizer-search').value.trim().toLocaleLowerCase(),status=$('organizer-status').value,type=$('organizer-type').value,model=$('organizer-model').value;
    const rows=state.jobs.filter(item=>{const saved=details(item.id);return ($('organizer-archived').checked || !saved.archived) && (!$('organizer-favorites').checked || saved.favorite) && (!status || item.status===status) && (!type || item.action===type) && (!model || item.request.model===model) && (!query || [item.id,item.request.model,item.request.seed,item.status,item.action,saved.title,saved.note,...saved.tags].join(' ').toLocaleLowerCase().includes(query));});
    const sort=$('organizer-sort').value;
    return rows.sort((a,b)=>sort==='name'?label(a).localeCompare(label(b)):sort==='oldest'?a.created_at.localeCompare(b.created_at):sort==='favorites' && details(a.id).favorite!==details(b.id).favorite?Number(details(b.id).favorite)-Number(details(a.id).favorite):b.created_at.localeCompare(a.created_at));
  }
  function toolbar() {
    const item=job(selectedId),saved=details(selectedId);
    for(const id of ['organizer-save','organizer-reload','organizer-archive']) $(id).disabled=!metadata || !item;
    $('organizer-project').disabled=!item?.project_id;
    $('organizer-reference').disabled=!metadata || !eligible(item);
    $('organizer-open-result').disabled=!item || (!['check_model','prompt'].includes(item.action) && !item.result_id);
    $('organizer-clear-reference').disabled=!metadata?.reference_job_id;
    $('organizer-export').disabled=!metadata || !matches().length;
    $('organizer-archive').textContent=saved.archived?'Restore job':'Archive job';
    const reference=job(metadata?.reference_job_id);
    $('organizer-reference-label').textContent=metadata?.reference_job_id?`Comparison reference: ${reference?label(reference):`saved job ${metadata.reference_job_id.slice(0,8)} (not loaded)`}`:'No comparison reference selected.';
  }
  function renderList() {
    toolbar();const rows=matches();page=Math.max(0,Math.min(page,Math.ceil(rows.length/20)-1));
    $('organizer-count').textContent=`${rows.length} of ${state.jobs.length} loaded jobs match`;
    $('organizer-page').textContent=`Page ${rows.length?page+1:0} of ${Math.ceil(rows.length/20)}`;
    $('organizer-prev').disabled=page===0;$('organizer-next').disabled=(page+1)*20>=rows.length;
    const models=[...new Set(state.jobs.map(item=>item.request.model))].sort(),previous=$('organizer-model').value;
    if(JSON.stringify(models)!==$('organizer-model').dataset.models) {$('organizer-model').dataset.models=JSON.stringify(models);$('organizer-model').replaceChildren(option('','All models'),...models.map(model=>option(model,model)));if(models.includes(previous)) $('organizer-model').value=previous;}
    const next=JSON.stringify([metadata?.revision,page,rows.map(item=>[item.id,item.status,item.completed_checks,item.completed_pairs]),selectedId]);if(next===signature)return;signature=next;
    const list=$('organizer-list');list.replaceChildren();if(!rows.length)list.append(node('p','muted','No jobs match these filters. Change the search or show archived jobs.'));
    for(const item of rows.slice(page*20,(page+1)*20)) {const saved=details(item.id),button=node('button','suite-card');button.type='button';button.dataset.jobId=item.id;button.setAttribute('aria-current',String(item.id===selectedId));button.append(node('strong','',`${saved.favorite?'★ ':''}${label(item)}${saved.archived?' · Archived':''}${item.id===metadata?.reference_job_id?' · Reference':''}`),node('span','',`${item.status.toUpperCase()} · ${item.request.model} · ${item.id.slice(0,8)}`),node('small','',saved.tags.join(' · ')));button.onclick=()=>fillEditor(item.id);list.append(button);}
  }
  function fillEditor(id) {
    selectedId=id;editGeneration++;const item=job(id);if(!item)return;const saved=details(id);
    $('organizer-title').value=saved.title;$('organizer-tags').value=saved.tags.join(', ');$('organizer-note').value=saved.note;$('organizer-favorite').checked=saved.favorite;
    $('organizer-editor-title').textContent=`Job details · ${id.slice(0,8)}`;
    $('organizer-job-info').textContent=`${item.request.model} · ${typeLabel(item)} · ${item.status}. Original ID: ${id}${item.project_id?` · Project at launch: ${item.project_title}`:""}`;
    feedback('Names and notes do not change model replies or scoring.');signature='';renderList();
  }
  async function reload() {apply(await api('/api/job-details'));}
  async function open(id) {
    if(!$('organizer-dialog').open)$('organizer-dialog').showModal();feedback('Loading saved jobs…');
    try {await refreshJobs();await reload();if(id && job(id)) fillEditor(id);else if(!selectedId && state.jobs.length) fillEditor(state.jobs[0].id);else feedback('Saved jobs loaded. Select an entry to edit its details.');} catch(error){feedback(error.message);}
  }
  async function save(archived) {
    if(!metadata || !job(selectedId) || !$('organizer-form').reportValidity())return;
    const id=selectedId,generation=editGeneration,record=details(id);
    $('organizer-save').disabled=true;
    try {const data=await api('/api/job-details',{job_id:id,expected_revision:metadata.revision,title:$('organizer-title').value,tags:$('organizer-tags').value.split(',').map(tag=>tag.trim()).filter(Boolean),note:$('organizer-note').value,favorite:$('organizer-favorite').checked,archived:archived??record.archived});apply(data);if(selectedId===id && generation===editGeneration) fillEditor(id);feedback('Job details saved. Original measured results were preserved.');}
    catch(error){feedback(error.message);}finally{toolbar();}
  }
  $('organizer-open').onclick=()=>open();$('skills-edit-details').onclick=()=>open(state.skillsJobId);
  $('organizer-refresh').onclick=async()=>{try{await refreshJobs();await reload();feedback('List refreshed. Editor drafts were kept.');}catch(error){feedback(error.message);}};
  $('organizer-reload').onclick=async()=>{try{await reload();fillEditor(selectedId);feedback('Latest saved details loaded. Unsaved edits were replaced.');}catch(error){feedback(error.message);}};
  $('organizer-form').onsubmit=event=>{event.preventDefault();save();};
  $('organizer-archive').onclick=()=>save(!details(selectedId).archived);
  for(const input of $('organizer-form').querySelectorAll('input,textarea')) input.addEventListener('input',()=>{editGeneration++;feedback('Unsaved details. Save to keep your changes.');});
  async function reference(id) {if(!metadata)return;try{apply(await api('/api/job-reference',{job_id:id,expected_revision:metadata.revision}));feedback(id?'Comparison reference saved. Use Compare with reference in Model skills.':'Comparison reference cleared.');}catch(error){feedback(error.message);}}
  $('organizer-reference').onclick=()=>reference(selectedId);$('organizer-clear-reference').onclick=()=>reference(null);
  $('organizer-project').onclick=()=>{const item=job(selectedId);if(item?.project_id){$('organizer-dialog').close();state.openProject?.(item.project_id);}};
  $('organizer-open-result').onclick=()=>{const item=job(selectedId);if(!item)return;$('organizer-dialog').close();if(item.action==='check_model')openSkills(item.id);else if(item.action==='prompt')state.openModelWorkspace?.(item.id);else if(item.result_id)location.hash=`${item.result_id.startsWith('campaign_')?'campaign':'run'}/${item.result_id}`;};
  for(const id of ['organizer-search','organizer-status','organizer-type','organizer-model','organizer-sort','organizer-favorites','organizer-archived']) $(id).addEventListener('input',()=>{page=0;signature='';renderList();});
  $('organizer-prev').onclick=()=>{page--;renderList();};$('organizer-next').onclick=()=>{page++;renderList();};
  $('organizer-export').onclick=()=>{const rows=matches();downloadSkillJson({format_version:1,kind:'saved_job_catalogue',loaded_jobs:state.jobs.length,exported_jobs:rows.length,reference_job_id:metadata.reference_job_id,jobs:rows.map(item=>({id:item.id,action:item.action,status:item.status,model:item.request.model,created_at:item.created_at,result_id:item.result_id,details:details(item.id)})),inference_requested:false,measured_results_modified:false,interpretation:'Matching loaded jobs across all pages. Labels and notes are human supplied. Original prompts, replies, scores and checkpoints are unchanged. This catalogue is not a new model evaluation.'},'saved-job-catalogue.json');};
  setInterval(()=>{if(!document.hidden && $('organizer-dialog').open)renderList();},2000);
  reload().catch(error=>feedback(`Saved details unavailable: ${error.message}`));
  return {open};
}
