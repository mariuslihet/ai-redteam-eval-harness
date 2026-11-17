import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {modeName, outcomeName, capabilityName, problemName, filterPairs, filterRuns, interval, nativeText, nativeTools, pairReasons, percent, tokenTotal, traceState} from '../src/redteam_eval/web/dashboard-core.mjs';

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
