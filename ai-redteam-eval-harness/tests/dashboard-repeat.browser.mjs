import {createRequire} from 'node:module';
import {mkdir,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base=process.env.DASHBOARD_URL || 'http://127.0.0.1:8765';
const output='reports/dashboard-verification';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:900}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
try {
  await page.goto(base);
  await page.waitForFunction(()=>document.querySelector('#engine-status').textContent.includes('ready'));
  await page.locator('#skills-open').click();
  const jobs=(await(await page.request.get(`${base}/api/jobs`)).json()).jobs;
  const eligible=jobs.filter(job=>job.action==='check_model' && ['complete','failed','cancelled'].includes(job.status) && job.result?.results?.length);
  assert.ok(eligible.length>=2,'Two actual saved tests are required');
  let selected=eligible.slice(0,2);
  if(process.env.DASHBOARD_REPEAT_LIVE==='1') {
    const source=eligible.find(job=>job.result.results.length===1 && job.request.model==='qwen2.5:7b-instruct-q4_K_M');
    assert.ok(source,'A one-question native source keeps live verification bounded');
    await page.locator('#skills-history').selectOption(source.id);
    const started=page.waitForResponse(response=>response.url().endsWith(`/api/jobs/${source.id}/repeat`) && response.status()===202);
    await page.locator('#skills-repeat').click();
    const job=await(await started).json();
    await page.waitForFunction(id=>document.querySelector('#skills-history').value===id && document.querySelector('#skills-status').textContent.startsWith('COMPLETE'),job.id,{timeout:180000});
    const repeated=await(await page.request.get(`${base}/api/jobs/${job.id}`)).json();
    assert.equal(repeated.result.results.length,1);
    assert.deepEqual(repeated.result.results.map(row=>row.question),source.result.results.map(row=>row.question));
    assert.deepEqual(await(await page.request.get(`${base}/api/jobs/${source.id}`)).json(),source);
    selected=[source,repeated];
    process.stdout.write(`Actual UI repeat: ${job.id}; native reply retained and source unchanged.\n`);
  }
  await page.locator('#skills-consistency > summary').click();
  await page.locator('#skills-consistency-run').click();
  await page.getByText('Choose 2–10 saved tests.',{exact:true}).waitFor();
  for(const job of selected) await page.locator(`#skills-consistency-choices input[value="${job.id}"]`).check();
  const responseWait=page.waitForResponse(response=>response.url().endsWith('/api/skill-consistency') && response.status()===200);
  await page.locator('#skills-consistency-run').click();
  const report=await(await responseWait).json();
  await page.getByRole('heading',{name:'Results across repeated runs',exact:true}).waitFor();
  assert.equal(report.inference_requested,false);
  assert.equal(report.source_modified,false);
  if(process.env.DASHBOARD_REPEAT_LIVE==='1') {
    assert.equal(report.included_replies,2);
    assert.equal(report.groups.length,1);
    assert.equal(report.groups[0].reply_count,2);
    assert.equal(report.excluded_replies.length,0);
  }
  for(const group of report.groups) for(const item of group.observations) assert.equal(item.response.text,item.response.response_envelope.message.content);
  const downloadWait=page.waitForEvent('download');
  await page.locator('#skills-consistency-export').click();
  await(await downloadWait).saveAs(`${output}/exported-repeat-report.json`);
  assert.deepEqual(JSON.parse(await readFile(`${output}/exported-repeat-report.json`,'utf8')),report);
  await page.locator('#skills-consistency-result').scrollIntoViewIfNeeded();
  await page.screenshot({path:`${output}/repeat-report-desktop.png`});
  await page.setViewportSize({width:390,height:844});
  await page.locator('#skills-consistency-result').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  await page.screenshot({path:`${output}/repeat-report-mobile.png`});
  await page.locator(`#skills-consistency-choices input[value="${selected[0].id}"]`).uncheck();
  assert.ok(await page.locator('#skills-consistency-export').isDisabled());
  assert.equal(await page.locator('#skills-consistency-result').innerText(),'');
  assert.deepEqual(errors,[]);
  process.stdout.write('Repeat report selection, validation, actual evidence, export, stale-result clearing and mobile checks passed.\n');
} finally {await browser.close();}
