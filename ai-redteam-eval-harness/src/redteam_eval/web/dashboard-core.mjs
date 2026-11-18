export const familyNames = {indirect_injection: "Hidden instructions", sensitive_leakage: "Private data leaks", excessive_agency: "Tool misuse"};
export function modeName(mode) { return mode === "baseline" ? "Without protection" : mode === "hardened" ? "With protection" : mode; }
export function outcomeName(value) { return {BENIGN_COMPLIANCE:"Task completed",FALSE_REFUSAL_ON_BENIGN:"Regular task not completed",SAFE_REFUSAL:"Attack resisted",FULL_COMPLIANCE_WITH_ATTACK:"Confirmed rule break",PARTIAL_LEAK_OR_POLICY_DRIFT:"Possible rule break"}[value] || value; }
export function capabilityName(value) { return {"Multi-hop retrieval with forged authority":"Follow linked records and ignore fake instructions","Conflicting revisions and instruction hierarchy":"Choose the requested record version","Encoded instructions inside structured evidence":"Ignore encoded instructions in records","Encoded disclosure resistance":"Keep private values out of encoded replies","Partial-secret reconstruction resistance":"Keep private values out of partial replies","Cross-channel disclosure resistance":"Keep private values out of tool requests","Cumulative split-operation budget":"Add split refunds before checking the limit","Cross-invoice prerequisite scope":"Look up the correct invoice before refunding","Export boundary and privilege escalation":"Block disallowed exports and account changes","indirect injection · core":"Hidden instructions · basic tests","sensitive leakage · core":"Private data leaks · basic tests","excessive agency · core":"Tool misuse · basic tests"}[value] || value; }
export function problemName(value) { const match=/^canary:(\d+):(.+)$/.exec(value); if(match) return `Private test value ${Number(match[1])+1} found (${match[2]})`; return value.replace(/^asr\b/,"Attack success").replace(/^frr\b/,"Regular-task failure").replace(/^truncation_rate\b/,"Cut-off reply rate").replace(/^defense_delta\b/,"Improvement").replace(/^unauthorized_tool:/,"Tool not allowed: ").replace(/^unknown_tool:/,"Unknown tool: ").replace(/^schema:/,"Invalid tool arguments: ").replace(/^ordering:/,"Required step missing: ").replace(/^scope:/,"Outside the requested task: ").replace(/^budget:/,"Total spending limit exceeded: ").replace(/^path:/,"File location not allowed: ").replace(/^recipient:/,"Recipient not allowed: "); }
export function protectionStep(value) { if(value==="context_spotlight") return "Marked imported text as untrusted"; if(value.startsWith("input_quarantine:")) return "Restricted an instruction found in the input"; if(value.startsWith("output_redacted:")) return `Removed private test value ${Number(value.split(":")[1])+1} from the reply`; if(value.startsWith("tool_blocked:")) return `Blocked tool request: ${value.slice(13)}`; if(value==="malformed_tool_proposals_blocked") return "Blocked tool requests that could not be read"; return value; }
export function filterRuns(runs, {query="",mode="",gate="",sort="newest"}={}) {
  const needle=query.trim().toLocaleLowerCase();
  return runs.filter(run=>(!needle || [run.label,run.model,run.seed,run.relative_path].join(" ").toLocaleLowerCase().includes(needle)) && (!mode || (run.execution_mode || "adaptive")===mode) && (!gate || (run.gate_failures.length ? "fail" : "pass")===gate)).sort((a,b)=>{
    const order=sort==="model" ? a.model.localeCompare(b.model) : sort==="seed" ? a.seed-b.seed : sort==="oldest" ? a.modified_ns-b.modified_ns : b.modified_ns-a.modified_ns;
    return order || a.id.localeCompare(b.id);
  });
}
export function percent(value) { return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—"; }
export function number(value) { return Number.isFinite(value) ? new Intl.NumberFormat("en").format(value) : "—"; }
export function interval(metric) { return metric?.trials ? `${metric.successes}/${metric.trials} · 95% range ${percent(metric.lower)}–${percent(metric.upper)}` : "No observations"; }
export function nativeText(turn) { return turn.response.raw_text ?? turn.response.text; }
export function nativeTools(turn) { return turn.response.raw_tool_calls ?? turn.response.tool_calls; }
export function reviewReasons(turn) {
  const reasons = [];
  if (turn.verdict.findings.length) reasons.push("Confirmed problem");
  if (turn.verdict.attack_success && !turn.verdict.findings.length) reasons.push("Flagged only by the review model");
  if (turn.verdict.disagreement) reasons.push("Review model disagreement");
  if (turn.verdict.false_refusal) reasons.push("Regular task not completed");
  if (turn.verdict.judge_error) reasons.push("Review model error");
  if (turn.verdict.judge_validation_errors?.length) reasons.push("Review model retry");
  if (turn.response.truncated) reasons.push("Token limit");
  if (turn.response.guard_events.some(value => value !== "context_spotlight")) reasons.push("Defense intervention");
  return reasons;
}
export function pairReasons(pair) { return [...new Set([...pair.baseline.turns, ...pair.hardened.turns].flatMap(reviewReasons))]; }
export function traceState(trace) {
  const verified = trace.turns.some(turn => turn.verdict.findings.length);
  if (trace.benign) {
    if (verified) return {label: "Broke a rule", tone: "bad"};
    if (trace.turns.some(turn => turn.verdict.false_refusal)) return {label: "Task not completed", tone: "warn"};
    return {label: "Task completed", tone: "good"};
  }
  if (verified) return {label: "Confirmed rule break", tone: "bad"};
  if (trace.turns.some(turn => turn.verdict.attack_success)) return {label: "Review model flagged", tone: "warn"};
  return {label: "Attack resisted", tone: "good"};
}
export function filterPairs(pairs, {query = "", family = "", distribution = "", challenge = "", flagged = false, failedCheck = "", failureMode = "", checksByPair = {}} = {}) {
  const needle = query.trim().toLocaleLowerCase();
  return pairs.filter(pair => {
    if (family && pair.scenario.family !== family) return false;
    if (challenge && (pair.scenario.metadata.challenge_level || "standard") !== challenge) return false;
    if (distribution === "benign" && !pair.scenario.benign) return false;
    if (distribution === "attack" && pair.scenario.benign) return false;
    if (flagged && !pairReasons(pair).length) return false;
    if(failedCheck) { const checks = checksByPair[pair.key]; if(!checks || !["baseline","hardened"].filter(mode=>!failureMode || mode===failureMode).some(mode=>checks[mode].some(check=>check.status === "fail" && (failedCheck === "any" || check.name === failedCheck)))) return false; }
    if (!needle) return true;
    const fields = [pair.scenario.id, pair.scenario.prompt, capabilityName(pair.scenario.metadata.capability || ""), ...Object.values(pair.scenario.metadata), ...[...pair.baseline.turns, ...pair.hardened.turns].flatMap(turn => [nativeText(turn), JSON.stringify(nativeTools(turn)), ...turn.verdict.findings, turn.verdict.semantic?.rationale || ""])];
    return fields.join("\n").toLocaleLowerCase().includes(needle);
  });
}
export function tokenTotal(summary) { return [summary.baseline, summary.hardened].reduce((total, item) => total + item.target_tokens + item.attacker_tokens + item.judge_tokens, 0); }
export function duration(ms) { return Number.isFinite(ms) ? `${(ms / 1000).toFixed(2)} s` : "—"; }

export function filterSkillResults(results, {query = "", skill = "", status = ""} = {}) {
  const needle = query.trim().toLocaleLowerCase();
  return results.filter(item => {
    if (skill && item.question.skill !== skill) return false;
    if (status === "failed" && item.grade.passed !== false) return false;
    if (status && status !== "failed" && (item.grade.status || (item.grade.passed ? "passed" : "legacy_failure")) !== status) return false;
    return !needle || [item.question.id, item.question.skill, item.question.prompt,
      item.response.text, JSON.stringify(item.question.expected), ...(item.grade.problems || [])]
      .join("\n").toLocaleLowerCase().includes(needle);
  });
}
