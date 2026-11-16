import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {filterPairs, interval, nativeText, nativeTools, pairReasons, percent, tokenTotal, traceState} from '../src/redteam_eval/web/dashboard-core.mjs';

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
