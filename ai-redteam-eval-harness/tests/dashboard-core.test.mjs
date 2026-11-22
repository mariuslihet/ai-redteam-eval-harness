import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {modeName, outcomeName, capabilityName, problemName, filterPairs, filterRuns, filterSkillResults, filterSkillJobs, interval, nativeText, nativeTools, pairReasons, percent, tokenTotal, traceState} from '../src/redteam_eval/web/dashboard-core.mjs';

const recording = JSON.parse(readFileSync(new URL('./fixtures/measured-qwen-run.json', import.meta.url)));
const pairs = recording.results;

test('filters retain exact measured pairs and threat/control separation', () => {
  assert.equal(filterPairs(pairs).length, 6);
  assert.equal(filterPairs(pairs, {distribution:'attack'}).length, 3);
  assert.equal(filterPairs(pairs, {distribution:'benign'}).length, 3);
  assert.equal(filterPairs(pairs, {challenge:'standard'}).length,6);
  assert.equal(filterPairs(pairs, {challenge:'advanced'}).length,0);
  for (const pair of pairs) {
    assert.ok(filterPairs(pairs, {query:pair.scenario.id}).includes(pair));
    assert.ok(filterPairs(pairs, {family:pair.scenario.family}).includes(pair));
  }
  assert.equal(filterPairs(pairs, {query:'no matching actual scenario'}).length, 0);
});
test('native evidence search uses original model text and tool calls', () => {
  for (const pair of pairs) for (const trace of [pair.baseline,pair.hardened]) for (const turn of trace.turns) {
    assert.equal(nativeText(turn), turn.response.raw_text ?? turn.response.text);
    assert.deepEqual(nativeTools(turn), turn.response.raw_tool_calls ?? turn.response.tool_calls);
    const quote = nativeText(turn).trim().slice(0, 40);
    if (quote) assert.ok(filterPairs(pairs, {query:quote}).includes(pair));
  }
});
test('review queue follows evidence rather than a hardcoded scenario list', () => {
  assert.deepEqual(filterPairs(pairs,{flagged:true}), pairs.filter(p=>pairReasons(p).length));
  for(const pair of pairs) for(const trace of [pair.baseline,pair.hardened]) {
    if(trace.turns.some(t=>t.verdict.findings.length)) assert.equal(traceState(trace).tone,'bad');
  }
});
test('missing rates remain unknown and usage totals include all roles', () => {
  assert.equal(percent(null),'—'); assert.equal(percent(0),'0.0%');
  assert.equal(interval({trials:0}),'No observations');
  assert.ok(tokenTotal(recording.summary)>0);
});

test('failure filters distinguish modes, named checks and unknown results', () => {
  const key=pairs[0].key;
  const checksByPair={[key]:{baseline:[{name:'Requested task completed',status:'fail'}],hardened:[{name:'Requested task completed',status:'unknown'}]}};
  assert.deepEqual(filterPairs(pairs,{failedCheck:'any',checksByPair}),[pairs[0]]);
  assert.deepEqual(filterPairs(pairs,{failedCheck:'Requested task completed',failureMode:'baseline',checksByPair}),[pairs[0]]);
  assert.deepEqual(filterPairs(pairs,{failedCheck:'any',failureMode:'hardened',checksByPair}),[]);
  assert.deepEqual(filterPairs(pairs,{failedCheck:'Private data protected in original reply',checksByPair}),[]);
});

test('run library searches metadata and combines mode/gate filters without changing source order',()=>{
 const runs=[{id:'a',label:'First',model:'Qwen',seed:42,relative_path:'reports/first',modified_ns:1,execution_mode:'adaptive',gate_failures:[]},{id:'b',label:'Replay',model:'Other',seed:1729,relative_path:'reports/replay',modified_ns:2,execution_mode:'recorded_replay',gate_failures:['asr exceeded']}];
 assert.deepEqual(filterRuns(runs).map(r=>r.id),['b','a']);
 assert.deepEqual(filterRuns(runs,{query:'QWEN',gate:'pass'}).map(r=>r.id),['a']);
 assert.deepEqual(filterRuns(runs,{mode:'recorded_replay',gate:'fail',query:'1729'}).map(r=>r.id),['b']);
 assert.deepEqual(filterRuns(runs,{query:'not present'}),[]);
 assert.deepEqual(filterRuns(runs,{sort:'seed'}).map(r=>r.id),['a','b']);
 assert.deepEqual(runs.map(r=>r.id),['a','b']);
});

test('display labels use plain words while saved reply access stays exact',()=>{
 const labels=[modeName('hardened'),outcomeName('BENIGN_COMPLIANCE'),problemName('canary:0:base64'),capabilityName('Multi-hop retrieval with forged authority')];
 assert.ok(labels.every(label=>! /\b(benign|canary|hardened|provenance|lineage|semantic)\b/i.test(label)));
 assert.equal(labels[1],'Task completed');
 assert.equal(labels[2],'Private test value 1 found (base64)');
 assert.equal(nativeText(pairs[0].baseline.turns[0]),pairs[0].baseline.turns[0].response.raw_text ?? pairs[0].baseline.turns[0].response.text);
});


test('skill filters preserve recorded replies and keep unconfirmed failures distinct', () => {
  // Recorded replies, not a completion backend; expected outputs belong to checker tests.
  const source=JSON.parse(readFileSync(new URL('./fixtures/measured-qwen-run.json', import.meta.url)));
  const response=source.results[0].baseline.turns[0].response;
  const statuses=['passed','wrong_answer','format_error','incomplete','needs_review','unexpected_tool'];
  const rows=statuses.map((status,index)=>({question:{id:`filter-${index}`,skill:index % 2 ? 'Arithmetic' : 'JSON output',prompt:`Recorded response ${index}`,expected:{count:index}},response,grade:{status,passed:status==='passed',problems:[`reason-${index}`]}}));
  const original=JSON.stringify(rows);
  assert.equal(filterSkillResults(rows).length,6);
  assert.equal(filterSkillResults(rows,{status:'failed'}).length,5);
  assert.equal(filterSkillResults(rows,{status:'wrong_answer'}).length,1);
  assert.equal(filterSkillResults(rows,{status:'needs_review'})[0],rows[4]);
  assert.equal(filterSkillResults(rows,{skill:'Arithmetic',status:'failed'}).length,3);
  assert.equal(filterSkillResults(rows,{query:'REASON-2'})[0],rows[2]);
  assert.equal(filterSkillResults(rows,{query:'"count":3'})[0],rows[3]);
  assert.equal(filterSkillResults(rows,{query:response.text}).length,6);
  assert.equal(filterSkillResults(rows,{query:'no such saved answer'}).length,0);
  const legacy={...rows[1],grade:{passed:false,problems:[]}};
  assert.deepEqual(filterSkillResults([legacy],{status:'legacy_failure'}),[legacy]);
  assert.equal(JSON.stringify(rows),original);
});


test('skill history search combines saved metadata without changing jobs',()=>{
 const jobs=[{id:'a',action:'check_model',status:'complete',request:{model:'Local Qwen',seed:1729,test_suite:'extended',difficulty:'challenging',selected_skills:['Arithmetic']}},{id:'b',action:'check_model',status:'cancelled',request:{model:'Other model',seed:42}},{id:'c',action:'evaluate',status:'complete',request:{model:'Local Qwen'}}];
 const before=JSON.stringify(jobs);
 assert.equal(filterSkillJobs(jobs).length,2);
 for(const query of ['qwen','1729','EXTENDED','challenging','Arithmetic','complete']) assert.deepEqual(filterSkillJobs(jobs,query),[jobs[0]]);
 assert.deepEqual(filterSkillJobs(jobs,'standard'),[jobs[1]]);
 assert.deepEqual(filterSkillJobs(jobs,'cancelled'),[jobs[1]]);
 assert.equal(filterSkillJobs(jobs,'no matching test').length,0);
 assert.equal(JSON.stringify(jobs),before);
});


test('human job names tags and notes are searchable without modifying measured jobs',()=>{
 const jobs=[{id:'a',action:'check_model',status:'complete',request:{model:'Installed',seed:1}}];
 const before=JSON.stringify(jobs), details={a:{title:'Release review',tags:['stock'],note:'Check latest revision'}};
 for(const query of ['RELEASE','stock','latest revision']) assert.equal(filterSkillJobs(jobs,query,details)[0],jobs[0]);
 assert.equal(filterSkillJobs(jobs,'unknown name',details).length,0);
 assert.equal(JSON.stringify(jobs),before);
});
