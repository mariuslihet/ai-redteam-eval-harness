import {familyNames, percent, number, interval, nativeText, nativeTools, reviewReasons, pairReasons, traceState, filterPairs, tokenTotal, duration} from "./dashboard-core.mjs";

const $ = id => document.getElementById(id);
const state = {catalog: {runs: [], campaigns: [], issues: []}, run: null, campaign: null, view: "runs", page: 0, models: [], defaultModel: "", jobs: [], pair: null, busy: false, selectedId: null};
const node = (tag, className = "", text = null) => { const value = document.createElement(tag); if (className) value.className = className; if (text !== null) value.textContent = String(text); return value; };
const visible = (id, show) => $(id).classList.toggle("hidden", !show);
const badge = (text, tone = "neutral") => node("span", `status ${tone}`, text);
function announce(message) { $("announcements").textContent = message; }
async function api(path, payload) {
  const response = await fetch(path, {method: payload === undefined ? "GET" : "POST", cache: "no-store", headers: payload === undefined ? {} : {"Content-Type": "application/json", "X-Dashboard-Token": document.querySelector('meta[name="dashboard-token"]').content}, body: payload === undefined ? undefined : JSON.stringify(payload)});
  const value = await response.json();
  if (!response.ok) { const error = new Error(value.error || `Request failed (${response.status})`); error.status = response.status; error.kind = value.kind; throw error; }
  return value;
}
function showError(error) { visible("loading", false); $("error-text").textContent = error.message; visible("error", true); $("error-retry").textContent = error.status === 403 ? "Reload session" : "Return to results"; $("error-retry").onclick = () => error.status === 403 ? location.reload() : (location.hash = "runs", refresh()); }
function formError(error) { const box = $("run-form-error"); box.replaceChildren(node("span", "", error.message)); if (error.status === 403) { const reload = node("button", "secondary small", "Reload session"); reload.type = "button"; reload.onclick = () => location.reload(); box.append(node("br"), reload); } }
function option(value, text) { const item = node("option", "", text); item.value = value; return item; }
function runLabel(run) { const label = /^[a-f0-9]{32}$/.test(run.label) ? `Local evaluation ${run.label.slice(0, 6)}` : run.label; return `${label} · seed ${run.seed} · ${run.model}`; }

async function checkEngine() {
  $("engine-status").textContent = "Checking local engine";
  try {
    const data = await api("/api/models");
    state.models = data.models || []; state.defaultModel = data.default_model || "";
    $("engine-status").className = `status ${data.ready ? "good" : "warn"}`;
    $("engine-status").textContent = data.ready ? "Local engine ready" : "Engine unavailable";
    $("engine-status").title = data.error || "Installed local completion models";
    $("model-select").replaceChildren(...state.models.map(model => option(model.name, model.name)));
    if (state.models.some(model => model.name === state.defaultModel)) $("model-select").value = state.defaultModel;
    $("run-submit").disabled = !state.models.length;
    $("analyze-case").disabled = !state.models.length;
  } catch (error) { $("engine-status").className = "status warn"; $("engine-status").textContent = "Engine unavailable"; $("engine-status").title = error.message; }
}

async function refresh({quiet = false} = {}) {
  if (state.busy) return;
  state.busy = true;
  if (!quiet) $("results").inert = true;
  if (!quiet) { visible("loading", true); $("loading").setAttribute("aria-busy", "true"); }
  visible("error", false);
  try {
    state.catalog = await api("/api/catalog");
    $("run-count").textContent = state.catalog.runs.length;
    $("campaign-count").textContent = state.catalog.campaigns.length;
    visible("excluded-panel", Boolean(state.catalog.issues.length));
    $("excluded-label").textContent = `${state.catalog.issues.length} artifacts excluded or unavailable`;
    $("excluded-items").replaceChildren(...state.catalog.issues.map(issue => node("li", "", `${issue.relative_path}: ${issue.reason}`)));
    const hash = location.hash.slice(1).split("/");
    if (["runs", "campaigns", "review"].includes(hash[0])) state.view = hash[0];
    if (hash[0] === "campaign" && hash[1]) { state.view = "campaigns"; await select(hash[1]); }
    else if (hash[0] === "run" && hash[1]) { state.view = state.view === "review" ? "review" : "runs"; await select(hash[1]); }
    else {
      if (state.view === "campaigns") await select(state.catalog.campaigns[0]?.id);
      else await select(state.selectedId && state.catalog.runs.some(run => run.id === state.selectedId) ? state.selectedId : state.catalog.runs[0]?.id);
    }
  } catch (error) { showError(error); }
  finally { state.busy = false; $("results").inert = false; visible("loading", false); $("loading").setAttribute("aria-busy", "false"); }
}

async function select(identifier) {
  const selectionChanged = state.selectedId !== (identifier || null);
  state.selectedId = identifier || null;
  const isCampaign = state.view === "campaigns";
  const items = isCampaign ? state.catalog.campaigns : state.catalog.runs;
  $("run-select").replaceChildren(...items.map(item => option(item.id, isCampaign ? `${item.label} · ${item.seeds.length} seeds` : runLabel(item))));
  if (identifier) $("run-select").value = identifier;
  document.querySelectorAll(".nav-button").forEach(button => button.classList.toggle("active", button.dataset.view === state.view));
  $("page-title").textContent = state.view === "review" ? "Evidence review" : isCampaign ? "Campaign coverage" : "Measured runs";
  $("page-description").textContent = isCampaign ? "Evaluate seed-to-seed variability without hiding individual failures." : state.view === "review" ? "Inspect deterministic findings, model disagreements and incomplete benign tasks." : "Inspect security behavior, utility and the evidence behind every verdict.";
  $("selection-label").textContent = isCampaign ? "Measured campaign" : "Measured run";
  visible("empty", !identifier); visible("results", Boolean(identifier));
  visible("notice", false);
  $("empty").querySelector("h2").textContent = isCampaign ? "No campaigns yet" : "No measured runs yet";
  $("compare-open").disabled = isCampaign || state.catalog.runs.length < 2;
  $("export-run").disabled = isCampaign;
  if (!identifier) return;
  if (selectionChanged) state.page = 0;
  if (isCampaign) {
    state.campaign = state.catalog.campaigns.find(item => item.id === identifier);
    if (!state.campaign) throw new Error("Measured campaign not found. Refresh the results list.");
    state.run = null;
    renderCampaign();
  } else {
    state.run = await api(`/api/runs/${identifier}`); state.campaign = null;
    const thresholds = state.run.thresholds, failures = state.run.gate_failures;
    $("notice").replaceChildren(badge(failures.length ? "Saved thresholds failed" : "Saved thresholds passed", failures.length ? "bad" : "good"), node("span", "", ` Maximum ASR ${percent(thresholds.max_asr)} · maximum benign failure ${percent(thresholds.max_frr)} · minimum defense delta ${percent(thresholds.min_defense_delta)} · ${thresholds.use_upper_ci ? "confidence-bound" : "point-estimate"} grading.`));
    if (failures.length) { const list = node("ul"); failures.forEach(text => list.append(node("li", "", text))); $("notice").append(list); }
    visible("notice", true);
    $("selection-meta").textContent = `${state.run.target_model} · ${state.run.execution_mode === "recorded_replay" ? "Recorded attack replay" : "Adaptive evaluation"} · seed ${state.run.config.seed}`;
    renderMetrics(state.run.summary); renderFamilies(state.run.summary); renderQuality(state.run.summary);
    visible("campaign-panel", false); visible("cases-panel", true); visible("overview-panels", true); visible("metric-grid", true);
    if (state.view === "review") $("flagged-filter").checked = true;
    $("flagged-filter").disabled = state.view === "review";
    $("cases-title").textContent = state.view === "review" ? "Review queue" : "Scenario explorer";
    renderCases();
  }
}

function renderMetrics(summary) {
  const hard = summary.hardened, base = summary.baseline;
  const reduction = base.asr.rate - hard.asr.rate;
  const metrics = [
    ["Hardened attack success", percent(hard.asr.rate), `${interval(hard.asr)} · ${Math.abs(reduction * 100).toFixed(1)} pp ${reduction >= 0 ? "reduction" : "increase"} vs baseline`],
    ["Verified attack violations", percent(hard.deterministic_violation_rate.rate), interval(hard.deterministic_violation_rate)],
    ["Benign task completion", hard.frr.trials ? percent(1 - hard.frr.rate) : "—", `${hard.frr.trials - hard.frr.successes}/${hard.frr.trials} controls completed · ${percent(hard.frr.rate)} failure rate`],
    ["Reported model tokens", number(tokenTotal(summary)), "Target + attacker + judge · both modes"]
  ];
  $("metric-grid").replaceChildren(...metrics.map(([label, value, note]) => { const card = node("article", "metric-card"); card.append(node("div", "metric-label", label), node("div", "metric-value", value), node("div", "metric-note", note)); return card; }));
}
function renderFamilies(summary) {
  const rows = Object.entries(summary.breakdowns.family).map(([family, values]) => {
    const row = node("div", "family-row"); row.append(node("div", "family-name", familyNames[family] || family));
    const bars = node("div", "family-bars");
    for (const [mode, tone] of [["baseline", "base"], ["hardened", "hard"]]) { const progress = node("progress", tone); progress.max = 1; progress.value = values[mode].asr.rate; progress.setAttribute("aria-label", `${familyNames[family] || family} ${mode}: ${percent(values[mode].asr.rate)}, ${interval(values[mode].asr)}`); bars.append(progress); }
    const rates = node("div", "family-rates"); rates.append(node("div", "", percent(values.baseline.asr.rate)), node("div", "", percent(values.hardened.asr.rate))); row.append(bars, rates); return row;
  });
  $("family-chart").replaceChildren(...rows);
}
function renderQuality(summary) {
  const hard = summary.hardened, base = summary.baseline;
  const items = [["Semantic-only attack flags", `${hard.semantic_only_success_rate.successes}/${hard.semantic_only_success_rate.trials}`], ["Judge disagreements", number(base.disagreements + hard.disagreements)], ["Judge validation repairs", number((base.judge_repaired_turns || 0) + (hard.judge_repaired_turns || 0))], ["Target outputs cut by token limit", number(base.truncation_rate.successes + hard.truncation_rate.successes)], ["Mean hardened target latency", duration(hard.mean_target_latency_ms)]];
  $("quality-list").replaceChildren(...items.map(([label, value]) => { const row = node("div", "quality-row"); row.append(node("span", "", label), node("strong", "", value)); return row; }));
}
function renderCampaign() {
  const campaign = state.campaign;
  $("selection-meta").textContent = `${campaign.model} · ${campaign.seed_results.length}/${campaign.seeds.length} measured seeds`;
  visible("cases-panel", false); visible("campaign-panel", true);
  visible("overview-panels", Boolean(campaign.summary)); visible("metric-grid", Boolean(campaign.summary));
  if (campaign.summary) { renderMetrics(campaign.summary); renderFamilies(campaign.summary); renderQuality(campaign.summary); }
  $("campaign-state").className = `status ${campaign.status === "complete" ? campaign.gate_failures.length ? "bad" : "good" : "warn"}`;
  $("campaign-state").textContent = campaign.status === "complete" ? campaign.gate_failures.length ? "Gate failed" : "Complete" : campaign.status;
  $("seed-rows").replaceChildren(...campaign.seed_results.map(item => { const row = node("tr"); [item.seed, percent(item.summary.baseline.asr.rate), percent(item.summary.hardened.asr.rate), percent(item.summary.hardened.frr.rate)].forEach(value => row.append(node("td", "", value))); const gateCell = node("td"); gateCell.append(badge(item.gate_failures.length ? "Fail" : "Pass", item.gate_failures.length ? "bad" : "good")); const action = node("td"), button = node("button", "link-button", "Open measured run"); button.onclick = () => openRun(item.run_id); action.append(button); row.append(gateCell, action); return row; }));
  $("campaign-message").textContent = campaign.error || (campaign.summary ? `Worst seed hardened ASR: ${percent(Math.max(...campaign.seed_results.map(item => item.summary.hardened.asr.rate)))}. ${campaign.gate_failures.length} gate failures.` : "Campaign is incomplete. Only completed seed audits are shown; no pooled benchmark is claimed.");
}

function renderCases() {
  if (!state.run) return;
  const pairs = filterPairs(state.run.results, {query: $("case-search").value, family: $("family-filter").value, distribution: $("distribution-filter").value, challenge: $("challenge-filter").value, flagged: state.view === "review" || $("flagged-filter").checked});
  const pages = Math.max(1, Math.ceil(pairs.length / 8)); state.page = Math.min(state.page, pages - 1);
  $("case-count").textContent = `${pairs.length} of ${state.run.results.length} scenarios`;
  const shown = pairs.slice(state.page * 8, state.page * 8 + 8);
  $("case-rows").replaceChildren(...shown.map(pair => {
    const row = node("tr"), name = node("td"); name.append(node("div", "case-name", familyNames[pair.scenario.family] || pair.scenario.family), node("div", "case-sub", pair.scenario.id));
    if(pair.scenario.metadata.capability) { const capability=node("div","case-sub",`Advanced · ${pair.scenario.metadata.capability}`); capability.title=pair.scenario.metadata.capability; name.append(capability); }
    const distribution = node("td"); distribution.append(badge(pair.scenario.benign ? "Benign control" : "Threat", pair.scenario.benign ? "blue" : "neutral")); row.append(name, distribution);
    for (const mode of ["baseline", "hardened"]) { const status = traceState(pair[mode]); const cell = node("td"); cell.append(badge(status.label, status.tone)); row.append(cell); }
    const reasons = pairReasons(pair), evidence = node("td", "case-evidence", reasons.length ? reasons.slice(0, 2).join(" · ") : "No flagged evidence");
    const action = node("td"), button = node("button", "link-button", "Inspect →"); button.setAttribute("aria-label", `Inspect ${pair.scenario.family} ${pair.scenario.benign ? "control" : "attack"}`); button.onclick = () => inspect(pair); action.append(button); row.append(evidence, action); return row;
  }));
  visible("no-cases", !pairs.length); $("page-info").textContent = pairs.length ? `${state.page * 8 + 1}–${Math.min((state.page + 1) * 8, pairs.length)} of ${pairs.length}` : "0 scenarios";
  $("previous-page").disabled = state.page === 0; $("next-page").disabled = state.page + 1 >= pages;
}

function detailBlock(title, content) { const details = node("details"); details.append(node("summary", "", title), node("pre", "", content)); return details; }
function renderTurn(turn) {
  const card = node("article", "turn-card"), heading = node("div", "turn-heading");
  heading.append(node("strong", "", `Turn ${turn.depth} · ${turn.mutation}`), badge(turn.verdict.outcome.replaceAll("_", " "), turn.verdict.attack_success ? "warn" : "neutral")); card.append(heading);
  card.append(node("div", "response-label", "Native model reply"));
  const text = nativeText(turn), tools = nativeTools(turn);
  if (text) card.append(node("pre", "response-text", text));
  else card.append(node("p", "muted", tools.length ? "The model proposed tools without a textual reply." : "No textual reply or tool proposal was returned."));
  if (tools.length) card.append(node("div", "response-label", "Native tool proposals · not executed"), node("pre", "", JSON.stringify(tools, null, 2)));
  if (turn.response.raw_text !== null && turn.response.raw_text !== undefined && turn.response.raw_text !== turn.response.text) card.append(detailBlock("Delivered reply after policy enforcement", turn.response.text));
  if (turn.verdict.findings.length) { const list = node("ul", "finding-list"); turn.verdict.findings.forEach(finding => list.append(node("li", "", finding))); card.append(node("div", "response-label", "Deterministic findings"), list); }
  if (turn.verdict.semantic) { const judge = node("div", "evidence-box"); judge.append(node("strong", "", `Model judge · ${percent(turn.verdict.semantic.confidence)} confidence`), node("p", "", turn.verdict.semantic.rationale)); if (turn.verdict.semantic.evidence) judge.append(node("blockquote", "", turn.verdict.semantic.evidence)); card.append(judge); }
  if (turn.response.truncated) card.append(badge("Output reached token limit", "warn"));
  if (turn.response.guard_events.length) card.append(detailBlock("Defensive middleware events", turn.response.guard_events.join("\n")));
  const metadata = {model: turn.response.response_envelope?.model || "unreported", digest: turn.response.model_digest || "not pinned", input_tokens: turn.response.usage.input_tokens, output_tokens: turn.response.usage.output_tokens, token_counts_estimated: turn.response.usage.estimated, latency_ms: turn.response.latency_ms, finish_reason: turn.response.finish_reason || "unreported", parent_id: turn.parent_id};
  card.append(detailBlock("Native provenance, usage and lineage", JSON.stringify(metadata, null, 2)));
  if (turn.verdict.judge_outputs?.length) card.append(detailBlock("Actual local-judge outputs and validation errors", JSON.stringify({outputs: turn.verdict.judge_outputs, validation_errors: turn.verdict.judge_validation_errors}, null, 2)));
  return card;
}
function inspect(pair) {
  state.pair = pair;
  state.caseRunId = state.run.id;
  loadInspection(state.caseRunId, pair);
  $("case-heading").textContent = familyNames[pair.scenario.family] || pair.scenario.family;
  $("case-prompt").textContent = pair.scenario.prompt; $("case-document").textContent = pair.scenario.document;
  $("case-distribution").className = `status ${pair.scenario.benign ? "blue" : "neutral"}`;
  $("case-distribution").textContent = `${pair.scenario.benign ? "Matched benign control" : "Generated threat"} · ${pair.scenario.metadata.document_format}`;
  $("response-columns").replaceChildren(...["baseline", "hardened"].map(mode => { const column = node("section", "mode-column"); column.append(node("h3", "mode-heading", mode === "baseline" ? "Baseline target" : "Hardened target"), ...pair[mode].turns.map(renderTurn)); return column; }));
  renderAnalysis(); $("case-dialog").showModal(); $("case-dialog").scrollTop = 0;
}
async function loadInspection(runId, pair) {
  state.inspection = null; $("download-case").disabled = true;
  $("inspection-content").replaceChildren(); $("inspection-description").textContent = "Reading recorded case evidence…";
  try {
    const data = await api(`/api/runs/${runId}/cases/${encodeURIComponent(pair.key)}`);
    if (state.caseRunId !== runId || state.pair?.key !== pair.key) return;
    state.inspection = data; $("download-case").disabled = false;
    $("inspection-description").textContent = `${data.challenge_level} · ${data.capability}`;
    const box = $("inspection-content"), contract = node("div", "inspection-contract");
    contract.append(node("h4", "", "What this test requires"), node("p", "", `Allowed tools: ${data.allowed_tools.join(", ") || "none"}. Expected tool plan: ${data.expected_tools.join(" → ") || "no tool plan required"}.`), detailBlock("Expected public result · not a model response", data.expected_text), node("p", "muted", data.interpretation)); box.append(contract);
    const scroll = node("div", "table-scroll"); scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", "Capability checks for both modes");
    const table = node("table"), head = node("tr"); ["Capability check", "Baseline", "Hardened", "Evidence rule"].forEach(text=>head.append(node("th", "", text))); const thead = node("thead"); thead.append(head); const body = node("tbody");
    data.modes.baseline.checks.forEach((check,index)=>{ const row = node("tr"); row.append(node("td", "", check.name)); for(const mode of ["baseline","hardened"]) { const value = data.modes[mode].checks[index], cell=node("td"); cell.append(badge(value.status.replaceAll("_"," "),value.status === "pass" ? "good" : value.status === "fail" ? "bad" : "neutral")); row.append(cell); } row.append(node("td", "inspection-rule", check.evidence)); body.append(row); });
    table.append(thead,body); scroll.append(table); box.append(scroll);
    const policies = node("details", "inspection-detail"); policies.append(node("summary", "", `Registered tool policies (${data.tool_policies.length})`));
    for (const tool of data.tool_policies) policies.append(detailBlock(`${tool.name} · ${tool.risk} · ${data.allowed_tools.includes(tool.name) ? "allowed" : "not authorized"}`,JSON.stringify(tool,null,2)));
    box.append(policies);
    for(const mode of ["baseline","hardened"]) {
      const section=node("details", "inspection-detail"); section.append(node("summary", "", `${mode} · conversation timeline and boundary checks`));
      for(const turn of data.modes[mode].turns) { const item=node("article", "inspection-turn"); item.append(node("h4", "", `Turn ${turn.depth} · ${turn.mutation}`), node("p", "muted", `ID: ${turn.id} · parent: ${turn.parent_id || "root"}`), node("p", "", `Proposed tools: ${turn.native_tool_count} native → ${turn.delivered_tool_count} delivered. Text changed by guards: ${turn.guard_changed_text ? "yes" : "no"}. Tool proposals changed: ${turn.guard_changed_tools ? "yes" : "no"}.`), detailBlock("Exact user-message segments",JSON.stringify(turn.user_messages,null,2)), detailBlock("Original-output policy findings and leak checks",JSON.stringify({policy:turn.native_policy_findings,leaks:turn.native_leaks},null,2)), detailBlock("Delivered-output policy findings and leak checks",JSON.stringify({policy:turn.delivered_policy_findings,leaks:turn.delivered_leaks,recorded_findings:turn.recorded_findings},null,2)), detailBlock("Exact native model request",JSON.stringify(turn.request_envelope,null,2)), detailBlock("Exact native response envelope",JSON.stringify(turn.response_envelope,null,2))); section.append(item); }
      box.append(section);
    }
  } catch(error) { if(state.caseRunId === runId && state.pair?.key === pair.key) $("inspection-description").textContent = `Could not inspect recorded evidence: ${error.message}`; }
}
$("download-case").onclick = () => { if(!state.inspection || !state.pair) return; const blob=new Blob([JSON.stringify({inspection:state.inspection, measured_pair:state.pair},null,2)],{type:"application/json"}); const url=URL.createObjectURL(blob), link=node("a"); link.href=url; link.download=`case-${state.pair.scenario.id}.json`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); };
$("test-depth").onchange = event => { if(event.target.value === "advanced") { const form=$("run-form"); for(const [name,value] of Object.entries({cases_per_family:12,max_turns:3,max_tokens:1536,max_total_tokens:300000,max_run_seconds:1800})) form.elements.namedItem(name).value=value; form.dispatchEvent(new Event("input")); } };
function renderAnalysis() {
  const box = $("case-analysis"); box.replaceChildren(); box.className = "analysis-empty";
  const jobs = state.jobs.filter(job => job.action === "analyze" && job.request.run_id === state.caseRunId && job.request.pair_key === state.pair?.key);
  const job = jobs[0];
  if (!job) { box.textContent = "No model explanation has been requested for this case."; return; }
  if (job.status === "queued" || job.status === "running") { box.textContent = "The installed local model is developing an evidence-linked explanation…"; return; }
  if (job.status !== "complete" || !job.result?.analysis) { box.textContent = job.error || "No completed model explanation is available."; return; }
  box.className = "analysis-result";
  const analysis = job.result.analysis;
  box.append(node("p", "analysis-source", `Generated by ${job.result.model} · digest ${job.result.model_digest} · an analysis opinion, not a replacement verdict`), node("p", "", analysis.summary));
    for (const [key, title] of [["baseline_assessment", "Baseline behavior"], ["hardened_assessment", "Hardened behavior"], ["security_impact", "Security impact"], ["utility_tradeoff", "Effect on legitimate tasks"]]) {
      if (analysis[key]) box.append(node("h4", "", title), node("p", "", analysis[key]));
    }
  for (const item of analysis.observations) { box.append(node("h4", "", `${item.mode} · ${item.turn_id.split(":").slice(-1)[0]}`), node("p", "", item.claim), node("blockquote", "", item.quote)); }
  for (const [label, items] of [["Recommendations", analysis.recommendations], ["Limits of this explanation", analysis.limitations]]) { box.append(node("h4", "", label)); const list = node("ul"); items.forEach(item => list.append(node("li", "", item))); box.append(list); }
}

function openRun(identifier) { state.view = "runs"; location.hash = `run/${identifier}`; }
function newRun() { $("run-form-error").replaceChildren(); if (!state.models.length) $("run-form-error").textContent = "The local engine has no available completion model. Start Ollama and use Check engine."; $("run-dialog").showModal(); }
async function refreshJobs() {
  try {
    const data = await api("/api/jobs"); const before = state.jobs.map(job => `${job.id}:${job.status}`).join();
    state.jobs = data.jobs;
    visible("job-panel", Boolean(state.jobs.length));
    const focusedAction = document.activeElement?.dataset.jobAction;
    const ordered = [...state.jobs].sort((a,b) => Number(["running","queued"].includes(b.status)) - Number(["running","queued"].includes(a.status)));
    const cards = ordered.map(job => {
      const card = node("article", `job-card ${job.status === "failed" ? "failed" : ""}`), info = node("div", "job-info");
      info.append(node("h3", "", `${job.action === "analyze" ? "Local model explanation" : job.request.seeds?.length ? "Local campaign" : "Local evaluation"} · ${job.request.model}`));
      const label = job.action === "evaluate" ? `${job.completed_pairs}/${job.total_pairs} committed pairs` : "Evidence-linked analysis";
      info.append(node("p", "", `${job.status.toUpperCase()} · ${label}${job.execution ? ` · ${number(job.execution.total_tokens)} observed/restored tokens` : ""}`));
      if (job.status === "running" || job.status === "queued") { const progress = node("progress"); if (job.total_pairs) { progress.max = job.total_pairs; progress.value = job.completed_pairs; } progress.setAttribute("aria-label", `${job.status}: ${label}`); info.append(progress); }
      if (job.error) info.append(node("p", "job-error", job.error));
      const actions = node("div", "job-actions");
      if (["running", "queued"].includes(job.status)) { const cancel = node("button", "secondary small", "Cancel job"); cancel.onclick = async () => { try { await api(`/api/jobs/${job.id}/cancel`, {}); announce("Cancellation requested"); await refreshJobs(); } catch (error) { showError(error); } }; actions.append(cancel); }
      if (job.action === "evaluate" && ["failed", "cancelled"].includes(job.status)) { const resume = node("button", "secondary small", "Resume same settings"); resume.onclick = async () => { try { await api(`/api/jobs/${job.id}/resume`, {}); await refreshJobs(); } catch (error) { showError(error); } }; actions.append(resume); }
      if (job.result_id && job.status === "complete") { const open = node("button", "primary small", "Open measured result"); open.onclick = () => { state.view = job.result_id.startsWith("campaign_") ? "campaigns" : "runs"; location.hash = `${state.view === "campaigns" ? "campaign" : "run"}/${job.result_id}`; }; actions.append(open); }
      actions.querySelectorAll("button").forEach(button => { button.dataset.jobAction = `${job.id}:${button.textContent}`; });
      card.append(info, actions); return card;
    });
    $("jobs").replaceChildren(...cards.slice(0,1));
    $("older-jobs").replaceChildren(...cards.slice(1));
    visible("job-history", cards.length > 1);
    $("job-history-label").textContent = `Earlier local jobs (${Math.max(0, cards.length - 1)})`;
    if (focusedAction) [...document.querySelectorAll("[data-job-action]")].find(button => button.dataset.jobAction === focusedAction)?.focus({preventScroll:true});
    if (state.pair) renderAnalysis();
    if (before && before !== state.jobs.map(job => `${job.id}:${job.status}`).join()) await refresh({quiet: true});
  } catch (error) { if (!state.jobs.length) console.error(error.message); }
}

document.querySelectorAll(".nav-button").forEach(button => button.onclick = () => { state.view = button.dataset.view; state.selectedId = null; history.replaceState(null, "", `#${state.view}`); refresh(); });
document.querySelectorAll(".close-dialog").forEach(button => button.onclick = () => button.closest("dialog").close());
$("refresh").onclick = () => refresh(); $("check-engine").onclick = checkEngine;
$("new-run").onclick = newRun; $("empty-new-run").onclick = newRun;
$("run-select").onchange = event => { location.hash = `${state.view === "campaigns" ? "campaign" : "run"}/${event.target.value}`; };
for (const id of ["case-search", "family-filter", "distribution-filter", "challenge-filter", "flagged-filter"]) $(id).addEventListener(id === "case-search" ? "input" : "change", () => { state.page = 0; renderCases(); });
$("previous-page").onclick = () => { state.page--; renderCases(); }; $("next-page").onclick = () => { state.page++; renderCases(); };
$("export-run").onclick = () => { if (state.run) location.href = `/api/runs/${state.run.id}/download/telemetry.json`; };
$("run-form").addEventListener("input", () => { const form = new FormData($("run-form")); const seeds = String(form.get("campaign_seeds") || "").split(",").filter(value => value.trim()); const pairs = 6 * Number(form.get("cases_per_family")) * Math.max(1, seeds.length); $("run-estimate").textContent = `${pairs} paired scenarios · baseline + hardened`; });
$("run-form").onsubmit = async event => {
  event.preventDefault(); $("run-form-error").replaceChildren(); $("run-submit").disabled = true;
  try {
    const form = new FormData(event.target); const payload = {action: "evaluate", model: form.get("model"), use_judge: form.has("use_judge")};
    for (const name of ["seed", "cases_per_family", "max_turns", "max_tokens", "max_total_tokens", "max_run_seconds"]) payload[name] = Number(form.get(name));
    const seeds = String(form.get("campaign_seeds") || "").trim();
    if (seeds) { if (!/^\d+(\s*,\s*\d+)+$/.test(seeds)) throw new Error("Enter at least two integer seeds separated by commas."); payload.seeds = seeds.split(",").map(value => Number(value.trim())); }
    await api("/api/jobs", payload); $("run-dialog").close(); announce("Real local evaluation queued"); await refreshJobs();
  } catch (error) { formError(error); }
  finally { $("run-submit").disabled = !state.models.length; }
};
$("analyze-case").onclick = async () => { if (!state.caseRunId || !state.pair) return; try { await api("/api/jobs", {action: "analyze", model: $("model-select").value || state.defaultModel, run_id: state.caseRunId, pair_key: state.pair.key}); await refreshJobs(); renderAnalysis(); } catch (error) { $("case-analysis").textContent = error.message; } };
$("compare-open").onclick = () => { for (const id of ["compare-previous", "compare-current"]) $(id).replaceChildren(...state.catalog.runs.map(run => option(run.id, runLabel(run)))); $("compare-current").value = state.run.id; $("comparison-result").textContent = "Only matched scenarios, search budgets and scoring configurations can be compared."; $("compare-dialog").showModal(); };
$("compare-submit").onclick = async () => { const box = $("comparison-result"); box.textContent = "Comparing measured outcomes…"; try { const data = await api("/api/compare", {previous: $("compare-previous").value, current: $("compare-current").value}); box.replaceChildren(badge(data.gate_failures.length ? "Regression gate failed" : "No measured regression", data.gate_failures.length ? "bad" : "good")); const table = node("table"), head = node("tr"); ["Metric", "Previous", "Current", "Change"].forEach(text => head.append(node("th", "", text))); const thead = node("thead"); thead.append(head); const body = node("tbody"); for (const [metric, value] of Object.entries(data.metrics)) { const row = node("tr"); [metric, percent(value.previous.rate), percent(value.current.rate), percent(value.change.delta)].forEach(text => row.append(node("td", "", text))); body.append(row); } table.append(thead, body); box.append(table, node("p", "muted", data.interpretation)); } catch (error) { box.textContent = error.message; } };
window.addEventListener("hashchange", () => refresh());
await Promise.allSettled([refresh(), checkEngine(), refreshJobs()]);
setInterval(() => { if (!document.hidden) refreshJobs(); }, 2000);
setInterval(() => { if (!document.hidden && !$("case-dialog").open && !$("run-dialog").open) refresh({quiet: true}); }, 15000);
