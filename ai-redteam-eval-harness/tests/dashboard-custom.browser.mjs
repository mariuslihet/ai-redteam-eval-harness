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
const definitions=[
  {name:'Custom arithmetic',prompt:'There are 9 shelves with 8 books each. 5 books are removed. How many remain?',answer_type:'integer',expected:'67'},
  {name:'Exact record',prompt:'Return the ID for the approved record: [{"id":"R-701","approved":false},{"id":"R-902","approved":true}]',answer_type:'text',expected:'R-902'},
  {name:'Record summary',prompt:'Return JSON with key "total" equal to 9 times 7 and key "valid" equal to false.',answer_type:'json',expected:'{"total":63,"valid":false}'},
];
try {
  await page.goto(base);
  await page.waitForFunction(()=>document.querySelector('#engine-status').textContent.includes('ready'));
  await page.locator('#skills-open').click();
  await page.locator('#skills-focus').selectOption('selected');
  await page.locator('#skills-suite').selectOption('custom');
  assert.equal(await page.locator('#skills-focus').inputValue(),'');
  assert.ok(await page.locator('#skills-difficulty').isDisabled());
  await page.locator('#custom-json-mode').click();
  await page.locator('#skills-custom-json').fill('[{"name":"Bad","prompt":"Return JSON","answer_type":"json","expected":"{\\"x\\":1,\\"x\\":2}"}]');
  await page.locator('#skills-preview').click();
  await page.getByText(/Repeated JSON keys/).waitFor();
  await page.locator('#skills-custom-import').setInputFiles({name:'my-tests.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(definitions))});
  await page.getByText('Definitions imported. Preview validates every expected answer before running.').waitFor();
  const exportWait=page.waitForEvent('download');
  await page.locator('#skills-custom-export').click();
  const download=await exportWait;
  await download.saveAs(`${output}/exported-custom-definitions.json`);
  assert.deepEqual(JSON.parse(await readFile(`${output}/exported-custom-definitions.json`,'utf8')),definitions);
  await page.locator('#skills-preview').click();
  await page.locator('#skills-plan-questions').waitFor();
  assert.match(await page.locator('#skills-plan').innerText(),/3 questions/);
  assert.match(await page.locator('#skills-plan').innerText(),/author supplied/);
  await page.locator('#skills-custom').scrollIntoViewIfNeeded();
  await page.screenshot({path:`${output}/custom-bench-desktop.png`});
  await page.setViewportSize({width:390,height:844});
  await page.locator('#skills-custom').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  await page.screenshot({path:`${output}/custom-bench-mobile.png`});
  await page.setViewportSize({width:1440,height:900});
  if(process.env.DASHBOARD_CUSTOM_LIVE==='1') {
    const started=page.waitForResponse(response=>response.url()===`${base}/api/jobs` && response.request().method()==='POST' && response.status()===202);
    await page.locator('#skills-submit').click();
    const job=await(await started).json();
    await page.waitForFunction(id=>document.querySelector('#skills-history').value===id && document.querySelector('#skills-status').textContent.startsWith('COMPLETE'),job.id,{timeout:180000});
    const saved=await(await page.request.get(`${base}/api/jobs/${job.id}`)).json();
    assert.equal(saved.total_checks,3);
    assert.equal(saved.completed_checks,3);
    assert.equal(saved.result.results.length,3);
    for(const row of saved.result.results) {
      assert.equal(row.response.text,row.response.response_envelope.message.content);
      assert.equal(row.response.request_envelope.body.messages.at(-1).content,row.question.prompt);
      assert.ok(row.question.rule.includes('author-supplied'));
    }
    await page.locator('#skills-restore').click();
    assert.deepEqual(JSON.parse(await page.locator('#skills-custom-json').inputValue()),definitions);
    await page.locator('#skills-preview').click();
    await page.locator('#skills-plan-questions').waitFor();
    const after=await(await page.request.get(`${base}/api/jobs/${job.id}`)).json();
    assert.deepEqual(after,saved);
    await page.locator('#skills-answers > details').first().locator('summary').first().click();
    await page.locator('#skills-answers').scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/custom-native-replies.png`});
    const failures=saved.result.results.filter(row=>["wrong_answer","format_error","unexpected_tool"].includes(row.grade.status));
    if(failures.length) {
      await page.waitForFunction(()=>!document.querySelector('#skills-retry').disabled);
      const retriedResponse=page.waitForResponse(response=>response.url().endsWith(`/api/jobs/${job.id}/retry`) && response.status()===202);
      await page.locator('#skills-retry').click();
      const retry=await(await retriedResponse).json();
      await page.waitForFunction(id=>document.querySelector('#skills-history').value===id && document.querySelector('#skills-status').textContent.startsWith('COMPLETE'),retry.id,{timeout:180000});
      const retried=await(await page.request.get(`${base}/api/jobs/${retry.id}`)).json();
      assert.equal(retried.total_checks,failures.length);
      assert.deepEqual(retried.request.custom_cases,definitions);
      assert.deepEqual(new Set(retried.result.results.map(row=>row.question.id)),new Set(failures.map(row=>row.question.id)));
      assert.deepEqual(await(await page.request.get(`${base}/api/jobs/${job.id}`)).json(),saved);
      process.stdout.write(`Actual failed-question retry passed: ${failures.length} fresh reply; source unchanged.\n`);
    }
    process.stdout.write(`Actual custom UI run: ${job.id}; ${saved.result.results.filter(row=>row.grade.passed).length}/3 passed.\n`);
  }
  await page.locator('#skills-suite').selectOption('extended');
  assert.ok(await page.locator('#skills-difficulty').isEnabled());
  assert.ok(await page.locator('#skills-custom').isHidden());
  assert.deepEqual(errors,[]);
  process.stdout.write('Custom definition import/export, invalid-answer rejection, preview, restore and mobile layout passed.\n');
} finally {await browser.close();}
