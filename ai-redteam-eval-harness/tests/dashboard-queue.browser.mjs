import {createRequire} from 'node:module';
import {mkdir,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url), {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base=process.env.DASHBOARD_URL || 'http://127.0.0.1:8766', output='reports/dashboard-verification';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome'}),page=await browser.newPage({viewport:{width:1440,height:900}});
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const get=async path=>(await(await page.request.get(`${base}${path}`)).json());
try {
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#engine-status').textContent.includes('ready'));
  let queue=await get('/api/queue');assert.equal(queue.items.length,0,'Use a fresh isolated reports root for queue browser verification.');
  const token=await page.locator('meta[name=dashboard-token]').getAttribute('content');
  const post=async(path,payload)=>{const response=await page.request.post(`${base}${path}`,{headers:{Origin:base,'X-Dashboard-Token':token},data:payload});assert.ok(response.ok(),await response.text());return response.json();};
  const model=(await get('/api/models')).models[0].name;
  const common={action:'check_model',model,seed:7744,cases_per_skill:1,max_tokens:512,max_total_tokens:10000,max_run_seconds:180};
  const first=(await post('/api/test-suites',{title:'Corrected event records',tags:['queue'],settings:{...common,test_suite:'data',difficulty:'challenging',selected_skills:['Reconciling event records']}})).suite;
  const second=(await post('/api/test-suites',{title:'Current batch record',tags:['queue'],settings:{...common,test_suite:'custom',custom_cases:[{name:'Find latest active batch',prompt:'For id B use only the highest revision. Return JSON with id and count from that record. Records: [{"id":"B","revision":1,"count":3},{"id":"C","revision":7,"count":99},{"id":"B","revision":3,"count":12},{"id":"B","revision":2,"count":8}].',answer_type:'json',expected:'{"id":"B","count":12}'}]}})).suite;
  await page.locator('#suites-open').click();await page.getByText('Library ready. Saving and loading do not start inference.',{exact:true}).waitFor();
  await page.locator(`#suite-list [data-suite-id="${first.id}"]`).click();await page.locator('#suite-queue').click();await page.locator('#queue-dialog').waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('#queue-items .queue-card').length===1);
  queue=await get('/api/queue');assert.ok(queue.paused);assert.equal(queue.items[0].suite_revision,1);
  await page.locator('#queue-dialog .close-dialog').click();await page.locator('#suite-dialog .close-dialog').click();
  await page.locator('#skills-open').click();await page.locator('#skills-focus').selectOption('Arithmetic');
  await page.locator('#queue-current').click();await page.waitForFunction(()=>document.querySelectorAll('#queue-items .queue-card').length===2);
  queue=await get('/api/queue');assert.equal((await get('/api/jobs')).jobs.length,0);
  for(const item of queue.items) {await page.locator(`[data-queue-id="${item.id}"]`).getByRole('button',{name:'Remove before starting',exact:true}).click();await page.getByText('Queue updated.',{exact:true}).waitFor();}
  await page.locator('#queue-dialog details > summary').click();
  for(const suite of [first,second]) await page.locator(`#queue-suite-choices input[value="${suite.id}"]`).check();
  await page.locator('#queue-add-suites').click();await page.getByText('Queue updated.',{exact:true}).waitFor();
  queue=await get('/api/queue');let pending=queue.items.filter(item=>item.status==='pending');assert.equal(pending.length,2);
  const last=pending[1];await page.locator(`[data-queue-id="${last.id}"]`).getByRole('button',{name:'Move up',exact:true}).click();await page.getByText('Queue updated.',{exact:true}).waitFor();
  assert.equal((await get('/api/queue')).items.filter(item=>item.status==='pending')[0].id,last.id);
  await page.locator(`[data-queue-id="${last.id}"]`).getByRole('button',{name:'Move down',exact:true}).click();await page.getByText('Queue updated.',{exact:true}).waitFor();
  queue=await get('/api/queue');pending=queue.items.filter(item=>item.status==='pending');
  await page.locator(`[data-queue-id="${pending[0].id}"]`).getByRole('button',{name:'Add another run',exact:true}).click();await page.getByText('Queue updated.',{exact:true}).waitFor();
  const repeated=(await get('/api/queue')).items.at(-1);await page.locator(`[data-queue-id="${repeated.id}"]`).getByRole('button',{name:'Remove before starting',exact:true}).click();await page.getByText('Queue updated.',{exact:true}).waitFor();
  // Editing the library after enqueue must not alter the captured queue revision.
  await post('/api/test-suites',{title:first.title,tags:first.tags,settings:{...first.settings,seed:9999},id:first.id,expected_revision:1});
  queue=await get('/api/queue');assert.equal(queue.items.find(item=>item.id===pending[0].id).setup.seed,7744);
  assert.equal((await get('/api/jobs')).jobs.length,0);
  if(process.env.DASHBOARD_QUEUE_LIVE==='1') {
    await page.locator('#queue-start').click();await page.waitForFunction(()=>document.querySelector('#queue-pause').disabled===false);
    await page.locator('#queue-pause').click();await page.getByText('Queue updated.',{exact:true}).waitFor();
    const firstId=pending[0].id;
    await page.waitForFunction(id=>document.querySelector(`[data-queue-id="${id}"] h3`)?.textContent.includes('COMPLETE'),firstId,{timeout:180000});
    queue=await get('/api/queue');assert.ok(queue.paused);assert.equal(queue.items.find(item=>item.id===pending[1].id).status,'pending');
    await page.locator('#queue-start').click();
    await page.waitForFunction(id=>document.querySelector(`[data-queue-id="${id}"] h3`)?.textContent.includes('COMPLETE'),pending[1].id,{timeout:180000});
    queue=await get('/api/queue');
    const actual=queue.items.filter(item=>item.job_id);assert.equal(actual.length,2);
    for(const item of actual) {const job=await get(`/api/jobs/${item.job_id}`);assert.equal(job.result.results.length,1);const reply=job.result.results[0].response;assert.equal(reply.text,reply.response_envelope.message.content);assert.equal(job.request.seed,7744);}
    assert.ok(actual[0].finished_at<=actual[1].started_at);
    process.stdout.write('Two actual UI jobs completed sequentially; pausing retained pending work and frozen suite revisions.\n');
  }
  await page.locator('#queue-refresh').click();
  await page.locator('#queue-items').scrollIntoViewIfNeeded();await page.screenshot({path:`${output}/work-queue-desktop.png`});
  await page.setViewportSize({width:390,height:844});await page.locator('#queue-items').scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);await page.screenshot({path:`${output}/work-queue-mobile.png`});
  await page.setViewportSize({width:1440,height:900});
  const downloadWait=page.waitForEvent('download');await page.locator('#queue-export').click();await(await downloadWait).saveAs(`${output}/exported-work-queue.json`);
  const exported=JSON.parse(await readFile(`${output}/exported-work-queue.json`,'utf8'));assert.equal(exported.items.length,5);assert.equal(exported.items.filter(item=>item.status==='cancelled').length,3);
  if(process.env.DASHBOARD_QUEUE_LIVE==='1') {
    const sources=await Promise.all(exported.items.filter(item=>item.job_id).map(async item=>[item.job_id,await get(`/api/jobs/${item.job_id}`)]));
    await page.locator('#queue-clear').click();await page.getByText('Queue updated.',{exact:true}).waitFor();
    assert.equal((await get('/api/queue')).items.length,0);
    for(const [id,before] of sources) assert.deepEqual(await get(`/api/jobs/${id}`),before);
  }
  assert.deepEqual(errors,[]);
  process.stdout.write('Suite queueing, setup capture, batch launch, reordering, cancellation, repeat, export, preserved results and responsive controls passed.\n');
} finally {await browser.close();}
