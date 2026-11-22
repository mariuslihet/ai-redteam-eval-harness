// Queue controls manage saved setups; replies come only from actual model jobs.
export function installQueueWorkbench({api,node,state,openSkills,skillFormSettings,refreshJobs,downloadSkillJson}) {
  const $=id=>document.getElementById(id);
  let snapshot=null, suites=[], busy=false, polling=false, signature='';
  const selected=new Set();
  function feedback(text) {$('queue-feedback').textContent=text;}
  function accept(data) {if(snapshot && data.revision<snapshot.revision) return;snapshot=data;render();}
  function render() {
    if(!snapshot) return;
    const pending=snapshot.items.filter(item=>item.status==='pending'), active=snapshot.items.find(item=>['starting','running'].includes(item.status));
    $('queue-open').textContent=`Work queue${pending.length?` · ${pending.length} waiting`:!snapshot.paused?' · running':''}`;
    $('queue-summary').textContent=`${snapshot.paused?'Paused':'Running'} · ${pending.length} waiting · ${active?1:0} queue jobs active · ${snapshot.pending_questions} questions waiting`;
    $('queue-message').textContent=snapshot.load_error || snapshot.message;
    $('queue-start').disabled=busy || !!snapshot.load_error || !snapshot.paused || !pending.length;
    $('queue-pause').disabled=busy || !!snapshot.load_error || snapshot.paused;
    $('queue-stop').disabled=busy || !!snapshot.load_error || !snapshot.active_job_id;
    $('queue-clear').disabled=busy || !!snapshot.load_error || !snapshot.items.some(item=>!['starting','running','pending'].includes(item.status));
    $('queue-error-policy').disabled=busy || !!snapshot.load_error;
    $('queue-error-policy').checked=snapshot.pause_on_error;
    $('queue-add-suites').disabled=busy || !!snapshot.load_error;
    const next=JSON.stringify([busy,snapshot.revision,snapshot.items.map(item=>[item.id,item.status,item.saved_replies])]);
    if(next===signature) return;signature=next;
    const focused=document.activeElement?.dataset?.queueAction;
    const box=$('queue-items');box.replaceChildren();
    if(!snapshot.items.length) box.append(node('p','muted','No queued work. Add a saved suite or use Add setup to queue in Model skills.'));
    const order=[...snapshot.items.filter(item=>['running','starting'].includes(item.status)),...pending,...snapshot.items.filter(item=>!['pending','running','starting'].includes(item.status)).reverse()];
    for(const item of order) {
      const card=node('article','queue-card');card.dataset.queueId=item.id;
      const position=pending.findIndex(row=>row.id===item.id);
      card.append(node('h3','',`${item.title} · ${item.status.toUpperCase()}${position>=0?` · position ${position+1}`:''}`),node('p','muted',`${item.setup.model} · ${item.setup.test_suite} · ${item.setup.difficulty} · seed ${item.setup.seed} · ${item.saved_replies??"Unknown"}/${item.question_count} replies saved`),node('p','muted',`Limits: ${item.setup.max_tokens} tokens per reply · ${item.setup.max_total_tokens} total tokens · ${item.setup.max_run_seconds} seconds after this job starts.`));
      if(item.project_id) card.append(node("p","muted",`Project at launch: ${item.project_title} · revision ${item.project_revision}`));
      if(item.suite_id) card.append(node('p','muted',`Saved suite revision ${item.suite_revision} · ${item.suite_id.slice(0,8)}`));
      if(item.job_id && typeof item.saved_replies==='number') {const progress=node('progress');progress.max=item.question_count;progress.value=item.saved_replies;progress.setAttribute('aria-label','Actual saved question replies');card.append(progress);}
      if(item.error) card.append(node('p','',item.error));
      const actions=node('div','queue-controls');
      function button(label,callback,disabled=false) {const control=node('button','secondary small',label);control.type='button';control.disabled=disabled || busy || !!snapshot.load_error;control.dataset.queueAction=`${item.id}:${label}`;control.onclick=callback;actions.append(control);}
      if(item.status==='pending') {button('Move up',()=>act('move_up',{item_id:item.id}),position===0);button('Move down',()=>act('move_down',{item_id:item.id}),position===pending.length-1);button('Remove before starting',()=>act('cancel',{item_id:item.id}));}
      button('Add another run',()=>act('repeat',{item_id:item.id}),busy || !!snapshot.load_error);
      if(item.job_id) button('Open actual replies',async()=>{await refreshJobs();if(!state.jobs.some(job=>job.id===item.job_id)) {feedback('This saved job is not loaded in the current job history. Its original files have not been removed.');return;}$('queue-dialog').close();openSkills(item.job_id);});
      card.append(actions);box.append(card);
    }
    if(focused) [...box.querySelectorAll('[data-queue-action]')].find(button=>button.dataset.queueAction===focused)?.focus({preventScroll:true});
  }
  async function refresh() {
    if(polling) return;polling=true;
    try {accept(await api('/api/queue'));} catch(error) {feedback(error.message);} finally {polling=false;}
  }
  async function operation(path,payload) {
    if(busy) throw new Error('Another queue change is in progress. Try again.');busy=true;render();feedback('Updating queue…');
    try {accept(await api(path,payload));feedback('Queue updated.');await refreshJobs();}
    catch(error) {feedback(error.message);await refresh();throw error;}
    finally {busy=false;render();}
  }
  async function act(action,extra={}) {if(!snapshot) return;try {await operation('/api/queue/action',{action,expected_revision:snapshot.revision,...extra});} catch(error) {feedback(error.message);}}
  async function refreshSuites() {
    const data=await api('/api/test-suites');suites=data.suites.filter(suite=>!suite.archived).sort((a,b)=>Number(b.favorite)-Number(a.favorite) || a.title.localeCompare(b.title));
    $('queue-suite-choices').replaceChildren();
    if(!suites.length) $('queue-suite-choices').append(node('p','muted','Save a setup in Suite library first.'));
    for(const suite of suites) {const label=node('label'),input=node('input');input.type='checkbox';input.value=suite.id;input.checked=selected.has(suite.id);input.onchange=()=>{if(input.checked) selected.add(suite.id);else selected.delete(suite.id);};label.append(input,node('span','',`${suite.favorite?'★ ':''}${suite.title} · revision ${suite.revision} · ${suite.settings.model}`));$('queue-suite-choices').append(label);}
    for(const id of selected) if(!suites.some(suite=>suite.id===id)) selected.delete(id);
  }
  async function open() {if(!$('queue-dialog').open) $('queue-dialog').showModal();await refresh();try {await refreshSuites();} catch(error) {feedback(error.message);}}
  async function enqueueSuite(selection) {
    await refresh();if(!snapshot) throw new Error('Queue is unavailable.');
    await operation('/api/queue/suites',{expected_revision:snapshot.revision,suites:[selection]});await open();
  }
  $('queue-open').onclick=open;$('queue-refresh').onclick=refresh;
  $('queue-start').onclick=()=>act('start');$('queue-pause').onclick=()=>act('pause');$('queue-stop').onclick=()=>act('stop_current');
  $('queue-clear').onclick=()=>act('clear_finished');$('queue-error-policy').onchange=event=>act('set_error_policy',{pause_on_error:event.target.checked});
  $('queue-export').onclick=()=>{if(snapshot) downloadSkillJson(snapshot,'work-queue-summary.json');};
  $('queue-refresh-suites').onclick=async()=>{try {await refreshSuites();feedback('Suite choices refreshed.');} catch(error) {feedback(error.message);}};
  $('queue-add-suites').onclick=async()=>{try {const choices=suites.filter(suite=>selected.has(suite.id));if(!choices.length || choices.length>10) throw new Error('Choose one to ten saved suites.');await operation('/api/queue/suites',{expected_revision:snapshot.revision,suites:choices.map(suite=>({id:suite.id,revision:suite.revision}))});selected.clear();await refreshSuites();} catch(error) {feedback(error.message);}};
  $('queue-current').onclick=async()=>{try {const settings=skillFormSettings();await refresh();if(!snapshot) throw new Error('Queue is unavailable.');await operation('/api/queue/add',{expected_revision:snapshot.revision,title:`${settings.test_suite} setup · seed ${settings.seed}`,settings});await open();} catch(error) {$('skills-error').textContent=error.message;}};
  setInterval(()=>{if(!document.hidden && ($('queue-dialog').open || (snapshot && !snapshot.paused && snapshot.items.some(item=>['pending','starting','running'].includes(item.status))))) refresh();},2000);
  refresh();
  return {enqueueSuite,open};
}
