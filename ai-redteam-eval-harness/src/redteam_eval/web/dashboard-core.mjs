export const familyNames = {indirect_injection: "Indirect injection", sensitive_leakage: "Sensitive leakage", excessive_agency: "Tool agency"};
export function percent(value) { return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—"; }
export function number(value) { return Number.isFinite(value) ? new Intl.NumberFormat("en").format(value) : "—"; }
export function interval(metric) { return metric?.trials ? `${metric.successes}/${metric.trials} · 95% CI ${percent(metric.lower)}–${percent(metric.upper)}` : "No observations"; }
export function nativeText(turn) { return turn.response.raw_text ?? turn.response.text; }
export function nativeTools(turn) { return turn.response.raw_tool_calls ?? turn.response.tool_calls; }
export function reviewReasons(turn) {
  const reasons = [];
  if (turn.verdict.findings.length) reasons.push("Verified finding");
  if (turn.verdict.attack_success && !turn.verdict.findings.length) reasons.push("Semantic-only flag");
  if (turn.verdict.disagreement) reasons.push("Judge disagreement");
  if (turn.verdict.false_refusal) reasons.push("Benign task incomplete");
  if (turn.verdict.judge_error) reasons.push("Judge error");
  if (turn.verdict.judge_validation_errors?.length) reasons.push("Judge repair");
  if (turn.response.truncated) reasons.push("Token limit");
  if (turn.response.guard_events.some(value => value !== "context_spotlight")) reasons.push("Defense intervention");
  return reasons;
}
export function pairReasons(pair) { return [...new Set([...pair.baseline.turns, ...pair.hardened.turns].flatMap(reviewReasons))]; }
export function traceState(trace) {
  const verified = trace.turns.some(turn => turn.verdict.findings.length);
  if (trace.benign) {
    if (verified) return {label: "Unsafe", tone: "bad"};
    if (trace.turns.some(turn => turn.verdict.false_refusal)) return {label: "Incomplete", tone: "warn"};
    return {label: "Completed", tone: "good"};
  }
  if (verified) return {label: "Verified violation", tone: "bad"};
  if (trace.turns.some(turn => turn.verdict.attack_success)) return {label: "Judge flagged", tone: "warn"};
  return {label: "Resisted", tone: "good"};
}
export function filterPairs(pairs, {query = "", family = "", distribution = "", challenge = "", flagged = false} = {}) {
  const needle = query.trim().toLocaleLowerCase();
  return pairs.filter(pair => {
    if (family && pair.scenario.family !== family) return false;
    if (challenge && (pair.scenario.metadata.challenge_level || "standard") !== challenge) return false;
    if (distribution === "benign" && !pair.scenario.benign) return false;
    if (distribution === "attack" && pair.scenario.benign) return false;
    if (flagged && !pairReasons(pair).length) return false;
    if (!needle) return true;
    const fields = [pair.scenario.id, pair.scenario.prompt, ...Object.values(pair.scenario.metadata), ...[...pair.baseline.turns, ...pair.hardened.turns].flatMap(turn => [nativeText(turn), JSON.stringify(nativeTools(turn)), ...turn.verdict.findings, turn.verdict.semantic?.rationale || ""])];
    return fields.join("\n").toLocaleLowerCase().includes(needle);
  });
}
export function tokenTotal(summary) { return [summary.baseline, summary.hardened].reduce((total, item) => total + item.target_tokens + item.attacker_tokens + item.judge_tokens, 0); }
export function duration(ms) { return Number.isFinite(ms) ? `${(ms / 1000).toFixed(2)} s` : "—"; }
