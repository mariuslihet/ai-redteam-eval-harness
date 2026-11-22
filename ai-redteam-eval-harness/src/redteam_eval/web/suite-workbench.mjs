// Definition authoring and saved setups. This module never starts model jobs.
export function installSuiteWorkbench({api,node,option,state,openSkills,skillFormSettings,applySkillSettings,downloadSkillJson,enqueueSuite}) {
  const $=id=>document.getElementById(id), clone=value=>JSON.parse(JSON.stringify(value));
  let mode='visual', undo=null, suites=[], selected=null, draft=null;
  const status=text=>{$('suite-status').textContent=text;};
  function cases() {
    const value=JSON.parse($('skills-custom-json').value);
    if(!Array.isArray(value) || value.length>20 || value.some(item=>!item || typeof item!=='object' || Array.isArray(item) || Object.keys(item).some(key=>!['name','prompt','answer_type','expected'].includes(key)) || ['name','prompt','answer_type','expected'].some(key=>typeof item[key]!=='string') || !['integer','text','json'].includes(item.answer_type))) throw new Error('Use a JSON array of up to 20 definitions with name, prompt, answer_type and expected. Correct unsupported fields in the JSON editor.');
    return value;
  }
  function changed(value) {
    $('skills-custom-json').value=JSON.stringify(value,null,2);
    $('skills-custom-json').dispatchEvent(new Event('input',{bubbles:true}));
  }
  function replaceCases(value, remember=true) {
    if(remember) undo=$('skills-custom-json').value;
    changed(value); renderBuilder();
  }
  function editorState() {
    const custom=$('skills-suite').value==='custom';
    $('custom-builder').classList.toggle('hidden',mode!=='visual');
    $('custom-raw-panel').classList.toggle('hidden',mode!=='json');
    $('custom-visual-mode').setAttribute('aria-pressed',String(mode==='visual'));
    $('custom-json-mode').setAttribute('aria-pressed',String(mode==='json'));
    for(const input of $('custom-test-rows').querySelectorAll('input,textarea,select,button')) input.disabled=!custom || mode!=='visual' || input.dataset.blocked==='true';
  }
  function renderBuilder() {
    let rows; try {rows=cases();} catch(error) {mode='json'; editorState(); $('custom-builder-status').textContent=error.message; $('skills-error').textContent=error.message; return;}
    $('custom-test-rows').replaceChildren();
    rows.forEach((item,index)=>{
      const card=node('fieldset','custom-test-card'), legend=node('legend','',`Test ${index+1}`); card.append(legend);
      for(const [key,labelText] of [['name','Name'],['prompt','Task prompt'],['answer_type','Answer format'],['expected','Expected answer supplied by you']]) {
        const label=node('label','',labelText), input=node(key==='answer_type'?'select':key==='name'?'input':'textarea');
        if(key==='answer_type') input.append(...['integer','text','json'].map(kind=>option(kind,kind==='integer'?'Integer':kind==='text'?'Exact text':'JSON')));
        else {input.required=true; input.maxLength=key==='name'?80:key==='prompt'?8000:4000; if(key!=='name') input.rows=key==='prompt'?4:3;}
        input.value=item[key]; input.dataset.field=key;
        input.addEventListener('input',()=>{rows[index][key]=input.value; changed(rows);});
        label.append(input); card.append(label);
      }
      const actions=node('div','custom-mode-actions');
      for(const [text,action,blocked] of [
        ['Move up',()=>{[rows[index-1],rows[index]]=[rows[index],rows[index-1]];replaceCases(rows);},index===0],
        ['Move down',()=>{[rows[index+1],rows[index]]=[rows[index],rows[index+1]];replaceCases(rows);},index===rows.length-1],
        ['Duplicate',()=>{const copy=clone(rows[index]); let suffix=2; const base=copy.name.slice(0,65); do {copy.name=`${base} copy ${suffix++}`;} while(rows.some(row=>row.name.trim().toLowerCase()===copy.name.toLowerCase())); rows.splice(index+1,0,copy);replaceCases(rows);},rows.length>=20],
        ['Remove',()=>{rows.splice(index,1);replaceCases(rows);},false]]) {
        const button=node('button','secondary small',text); button.type='button'; button.setAttribute('aria-label',`${text} test ${index+1}`); button.dataset.blocked=String(blocked); button.onclick=action; actions.append(button);
      }
      card.append(actions); $('custom-test-rows').append(card);
    });
    $('custom-add-test').disabled=rows.length>=20; $('custom-undo').disabled=undo===null;
    $('custom-builder-status').textContent=`${rows.length} of 20 tests. Reordering keeps each test's identity. Duplicates need distinct names. No model replies are created here.`;
    editorState();
  }
  $('custom-add-test').onclick=()=>{try {const rows=cases(); if(rows.length>=20) return; let suffix=rows.length+1; while(rows.some(row=>row.name===`Test ${suffix}`)) suffix++; rows.push({name:`Test ${suffix}`,prompt:'',answer_type:'text',expected:''});replaceCases(rows);} catch(error) {$('skills-error').textContent=error.message;}};
  $('custom-undo').onclick=()=>{if(undo!==null) {const previous=undo; undo=null; $('skills-custom-json').value=previous; $('skills-custom-json').dispatchEvent(new Event('input',{bubbles:true}));renderBuilder();}};
  $('custom-visual-mode').onclick=()=>{mode='visual';renderBuilder();};
  $('custom-json-mode').onclick=()=>{mode='json';editorState();};
  $('skills-suite').addEventListener('change',()=>{if(mode==='visual') renderBuilder();else editorState();});
  $('skills-custom-json').addEventListener('change',()=>{undo=null;if(mode==='visual') renderBuilder();else editorState();});

  function currentSettings() {
    if(!$('skills-model').value && state.models.length) {$('skills-model').replaceChildren(...state.models.map(model=>option(model.name,model.name))); $('skills-model').value=state.defaultModel || state.models[0].name;}
    const settings=skillFormSettings(); settings.retry_of=null;
    return clone(settings);
  }
  function summary() {
    const settings=draft?.settings;
    $('suite-settings-summary').textContent=settings?`${settings.model} · ${settings.test_suite} · ${settings.difficulty || 'standard'} · seed ${settings.seed}. Loading fills the test form; starting inference is a separate action.`:'Select a suite or create one from your current test setup.';
    $('suite-settings-preview').textContent=settings?JSON.stringify(settings,null,2):'';
  }
  function fillEditor(value, saved=null) {
    selected=saved; draft=clone(value); for(const button of $('suite-list').querySelectorAll('[data-suite-id]')) button.setAttribute('aria-current',String(button.dataset.suiteId===saved?.id));
    $('suite-title').value=value.title || ''; $('suite-description').value=value.description || '';
    $('suite-tags').value=(value.tags || []).join(', '); $('suite-favorite').checked=!!value.favorite;
    $('suite-editor-heading').textContent=saved?`Edit suite · revision ${saved.revision}`:'New suite';
    for(const id of ['suite-load','suite-copy','suite-export','suite-archive','suite-reload','suite-queue']) $(id).disabled=!saved;
    $('suite-save').disabled=false; $('suite-update-setup').disabled=false;
    $('suite-archive').textContent=value.archived?'Restore suite':'Archive suite'; summary();
  }
  function renderLibrary() {
    const query=$('suite-search').value.trim().toLocaleLowerCase(), favorites=$('suite-favorites').checked, archived=$('suite-show-archived').checked;
    const rows=suites.filter(suite=>(archived || !suite.archived) && (!favorites || suite.favorite) && `${suite.title} ${suite.description} ${suite.tags.join(' ')}`.toLocaleLowerCase().includes(query)).sort((a,b)=>Number(b.favorite)-Number(a.favorite) || a.title.localeCompare(b.title));
    $('suite-count').textContent=`${rows.length} of ${suites.length} saved suites`;
    $('suite-list').replaceChildren();
    if(!rows.length) $('suite-list').append(node('p','muted','No suites match. Create one from a test setup or change the filters.'));
    for(const suite of rows) {const button=node('button','suite-card'); button.type='button';button.dataset.suiteId=suite.id;button.setAttribute('aria-current',String(suite.id===selected?.id));button.append(node('strong','',`${suite.favorite?'★ ':''}${suite.title}${suite.archived?' · Archived':''}`),node('span','',suite.description || `${suite.settings.test_suite} · ${suite.settings.model}`),node('small','',suite.tags.join(' · ')));button.onclick=()=>{fillEditor(suite,suite);status('Saved suite loaded for editing. No model call was made.');};$('suite-list').append(button);}
  }
  async function refreshLibrary() {const data=await api('/api/test-suites');suites=data.suites;renderLibrary();}
  function newFromSetup() {try {fillEditor({title:'',description:'',tags:[],favorite:false,archived:false,settings:currentSettings()});status('Current setup captured. Add a name and save.');} catch(error) {status(error.message);}}
  async function openLibrary(capture=false) {
    if(!$('suite-dialog').open) $('suite-dialog').showModal();status('Loading saved suites…');
    try {await refreshLibrary();if(capture) newFromSetup();else if(!draft && suites.length) fillEditor(suites[0],suites[0]);else if(!draft) {summary();$('suite-save').disabled=true;$('suite-update-setup').disabled=true;} if(!capture) status('Library ready. Saving and loading do not start inference.');} catch(error) {status(error.message);}
  }
  $('suites-open').onclick=()=>openLibrary(); $('suite-save-current').onclick=()=>openLibrary(true); $('suite-new').onclick=newFromSetup;
  for(const id of ['suite-search','suite-favorites','suite-show-archived']) $(id).addEventListener('input',renderLibrary);
  function metadata() {
    if(!draft?.settings) throw new Error('Create or select a suite first.');
    return {title:$('suite-title').value,description:$('suite-description').value,tags:$('suite-tags').value.split(',').map(tag=>tag.trim()).filter(Boolean),favorite:$('suite-favorite').checked,archived:!!draft.archived,settings:clone(draft.settings)};
  }
  async function saveDraft(archived) {
    if(!$('suite-editor').reportValidity()) return;
    $('suite-save').disabled=true; $('suite-archive').disabled=true;
    try {const definition=metadata();if(archived!==undefined) definition.archived=archived;
      const data=await api('/api/test-suites',{...definition,id:selected?.id || null,expected_revision:selected?.revision || 0});
      fillEditor(data.suite,data.suite); await refreshLibrary();status(`Suite saved · revision ${data.suite.revision}. No model call was made.`);
    } catch(error) {status(error.message);} finally {$('suite-save').disabled=false;$('suite-archive').disabled=!selected;}
  }
  for(const input of $('suite-editor').querySelectorAll('input,textarea')) input.addEventListener('input',()=>{if(draft) status('Unsaved suite details. Save to keep these changes.');});
  $('suite-editor').onsubmit=event=>{event.preventDefault();saveDraft();};
  $('suite-archive').onclick=()=>saveDraft(!draft.archived);
  $('suite-reload').onclick=async()=>{if(!selected) return;try {const id=selected.id;await refreshLibrary();const saved=suites.find(row=>row.id===id);if(!saved) throw new Error('Saved suite is no longer available.');fillEditor(saved,saved);status('Latest saved version loaded. Unsaved edits were replaced.');} catch(error) {status(error.message);}};
  $('suite-copy').onclick=()=>{try {const value=metadata();value.title=`${value.title.slice(0,70)} copy`;value.archived=false;fillEditor(value);status('Independent copy ready. Save to add it to the library.');} catch(error) {status(error.message);}};
  $('suite-update-setup').onclick=()=>{try {draft.settings=currentSettings();summary();status('Current setup captured. Save to update this suite.');} catch(error) {status(error.message);}};
  $('suite-export').onclick=()=>{if(selected) {const {id,revision,created_at,updated_at,...definition}=selected;downloadSkillJson({format_version:1,kind:'test_suite',definition},`${selected.title.replace(/[^a-zA-Z0-9_-]/g,'_') || 'test-suite'}.json`);}};
  $('suite-import').onchange=async event=>{const file=event.target.files[0];if(!file) return;try {if(file.size>65536) throw new Error('Suite imports must fit within 64 KiB.');const data=JSON.parse(await file.text());if(data.format_version!==1 || data.kind!=='test_suite' || !data.definition || typeof data.definition.title!=='string' || typeof data.definition.description!=='string' || !Array.isArray(data.definition.tags) || data.definition.tags.some(tag=>typeof tag!=='string') || !data.definition.settings || typeof data.definition.settings!=='object' || Array.isArray(data.definition.settings) || typeof data.definition.favorite!=='boolean' || typeof data.definition.archived!=='boolean' || Object.keys(data.definition).some(key=>!['title','description','tags','favorite','archived','settings'].includes(key))) throw new Error('Import an exported test-suite definition.');fillEditor({...data.definition,archived:false});status('Imported as a new draft. Save validates the complete setup before it can be loaded.');} catch(error) {status(error.message);} finally {event.target.value='';}};
  $('suite-queue').onclick=async()=>{if(!selected)return;try {await enqueueSuite({id:selected.id,revision:selected.revision});status('Saved suite revision added to the queue. Unsaved editor changes were not included.');} catch(error) {status(error.message);}};
  $('suite-load').onclick=()=>{if(!selected) return;$('suite-dialog').close();openSkills();applySkillSettings(clone(selected.settings));$('skills-error').textContent=`Loaded suite: ${selected.title}. ${$('skills-model').value?'Review the setup and run when ready.':'Choose an installed model before running.'} No model call was made.`;};
  renderBuilder();summary();
}
