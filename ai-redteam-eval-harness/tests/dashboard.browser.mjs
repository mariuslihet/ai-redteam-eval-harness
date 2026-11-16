/** End-to-end checks against an actual running dashboard; no intercepted responses.
 * PLAYWRIGHT_MODULE may point to an existing Playwright installation.
 * DASHBOARD_URL defaults to the local dashboard. Captures actual rendered evidence.
 */
import {createRequire} from 'node:module';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL || 'chrome'});
const base=process.env.DASHBOARD_URL || 'http://127.0.0.1:8765';
const output='reports/dashboard-verification';
await mkdir(output,{recursive:true});
const page=await browser.newPage({viewport:{width:1440,height:900}});
const errors=[],remote=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>{if(!request.url().startsWith(base)) remote.push(request.url());});
try {
  await page.goto(base);
  await page.locator('#case-rows tr').first().waitFor();
  assert.equal(await page.locator('.brand-mark,.brand-sub').count(),0);
  assert.ok(!(await page.locator('body').innerText()).includes('Everything stays on this computer.'));
  if (process.env.DASHBOARD_LIVE === '1') {
    await page.locator('#new-run').click();
    await page.waitForFunction(()=>document.querySelector('#model-select').value && !document.querySelector('#run-submit').disabled);
    await page.locator('[name=seed]').fill('1729');
    await page.locator('[name=max_turns]').fill('1');
    await page.locator('[name=use_judge]').uncheck();
    await page.locator('#run-submit').click();
    await page.locator('#run-dialog').waitFor({state:'hidden'});
    const result=page.locator('#jobs .job-card').first().getByRole('button',{name:'Open measured result'});
    await result.waitFor({timeout:180000}); await result.click();
    await page.waitForFunction(()=>location.hash === '#run/'+document.querySelector('#run-select').value && !document.querySelector('#results').inert);
    await page.locator('#case-rows button').first().click();
    await page.locator('#analyze-case').click();
    await page.getByRole('heading',{name:'Effect on legitimate tasks'}).waitFor({timeout:180000});
    await page.locator('#case-analysis').scrollIntoViewIfNeeded();
    await page.screenshot({path:`${output}/developed-explanation.png`,fullPage:false});
    await page.keyboard.press('Escape');
    process.stdout.write('Real UI evaluation and developed local-model explanation passed.\n');
  }
  const catalog=await (await page.request.get(`${base}/api/catalog`)).json();
  assert.ok(catalog.runs.length);
  const selected=await page.locator('#run-select').inputValue();
  const run=await (await page.request.get(`${base}/api/runs/${selected}`)).json();
  await page.screenshot({path:`${output}/desktop.png`,fullPage:true});
  await page.locator('#case-search').fill('no matching actual scenario');
  await page.locator('#no-cases').waitFor({state:'visible'});
  await page.locator('#case-search').fill('');
  await page.locator('#distribution-filter').selectOption('attack');
  assert.equal(await page.locator('#case-rows tr').count(),Math.min(8,run.results.filter(p=>!p.scenario.benign).length));
  await page.locator('#distribution-filter').selectOption('');
  await page.locator('#case-rows button').first().click();
  await page.locator('#case-dialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#case-prompt').innerText(),run.results[0].scenario.prompt);
  await page.locator('#inspection-content table tbody tr').first().waitFor();
  assert.equal(await page.locator('#inspection-content table tbody tr').count(),7);
  await page.getByText('Registered tool policies (4)',{exact:true}).click();
  await page.getByText('baseline · conversation timeline and boundary checks',{exact:true}).click();
  assert.ok(await page.getByText('Exact native model request',{exact:true}).first().isVisible());
  const downloadEvent=page.waitForEvent('download');
  await page.locator('#download-case').click();
  const download=await downloadEvent;
  await download.saveAs(`${output}/exported-case.json`);
  const {readFile}=await import('node:fs/promises');
  const exported=JSON.parse(await readFile(`${output}/exported-case.json`,'utf8'));
  assert.deepEqual(exported.measured_pair,run.results[0]);
  const observed=run.results[0].baseline.turns[0].response;
  const text=observed.raw_text ?? observed.text;
  if(text) assert.ok((await page.locator('#response-columns').innerText()).includes(text));
  await page.screenshot({path:`${output}/case-evidence.png`,fullPage:false});
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#case-dialog').evaluate(e=>e.open),false);
  const advanced=catalog.runs.find(run=>run.relative_path==='advanced-capabilities-20261116/telemetry.json');
  if(advanced) {
    await page.goto(`${base}#run/${advanced.id}`);
    await page.waitForFunction(id=>document.querySelector('#run-select').value === id && !document.querySelector('#results').inert, advanced.id);
    await page.locator('#challenge-filter').selectOption('advanced');
    assert.equal(await page.locator('#case-count').innerText(),'18 of 72 scenarios');
    await page.locator('#case-search').fill('Multi-hop retrieval with forged authority');
    assert.equal(await page.locator('#case-rows tr').count(),2);
    await page.locator('#case-rows button').first().click();
    await page.waitForFunction(()=>document.querySelector('#inspection-description').textContent.startsWith('advanced'));
    await page.screenshot({path:`${output}/advanced-inspect-desktop.png`,fullPage:false});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
    await page.screenshot({path:`${output}/advanced-inspect-mobile.png`,fullPage:false});
    await page.keyboard.press('Escape');
    await page.setViewportSize({width:1440,height:900});
    await page.locator('#case-search').fill('');
    await page.locator('#challenge-filter').selectOption('');
  }
  await page.locator('#compare-open').click();
  await page.locator('#compare-previous').selectOption(selected);
  await page.locator('#compare-current').selectOption(selected);
  await page.locator('#compare-submit').click();
  await page.getByText('No measured regression',{exact:true}).waitFor();
  await page.keyboard.press('Escape');
  if(catalog.campaigns.length) {
    await page.locator('[data-view=campaigns]').click();
    await page.locator('#seed-rows tr').first().waitFor();
    await page.screenshot({path:`${output}/campaigns.png`,fullPage:true});
    await page.locator('[data-view=runs]').click();
    await page.locator('#case-rows tr').first().waitFor();
  }
  if(await page.locator('#job-history').isVisible()) {
    await page.locator('#job-history-label').click();
    const action=page.locator('#older-jobs button').first();
    if(await action.count()) {
      await action.focus();
      const key=await action.getAttribute('data-job-action');
      await page.waitForTimeout(2200);
      assert.equal(await page.evaluate(()=>document.activeElement.dataset.jobAction),key);
    }
    await page.locator('#job-history-label').click();
  }
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  await page.screenshot({path:`${output}/mobile.png`,fullPage:true});
  await page.locator('#case-rows button').first().click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  await page.keyboard.press('Escape');
  await page.locator('#new-run').click();
  await page.locator('#test-depth').selectOption('advanced');
  assert.equal(await page.locator('[name=cases_per_family]').inputValue(),'12');
  assert.equal(await page.locator('[name=max_turns]').inputValue(),'3');
  assert.ok((await page.locator('#run-estimate').innerText()).includes('72'));
  await page.locator('[name=seed]').fill('20261116');
  await page.evaluate(()=>document.querySelector('meta[name=dashboard-token]').content='expired-session');
  await page.locator('#run-submit').click();
  await page.locator('#run-form-error').getByText(/session changed/).waitFor();
  await page.screenshot({path:`${output}/permission.png`,fullPage:false});
  await page.keyboard.press('Escape');
  await page.goto(base+'#run/run_ffffffffffffffffffffffff');
  await page.locator('#error').waitFor({state:'visible'});
  await page.screenshot({path:`${output}/error.png`,fullPage:false});
  assert.deepEqual(errors,[]); assert.deepEqual(remote,[]);
  process.stdout.write('Dashboard browser checks passed: real archive, filters, native evidence, comparison, campaigns, mobile, permission, error.\n');
} finally { await browser.close(); }
