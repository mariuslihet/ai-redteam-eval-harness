# Validation record

Validated locally on 21 November 2026 with Python 3.12.14. Changes remain local; no commits
or pushes were made during this feature extension.

## Current verification

| Check | Evidence |
|---|---|
| Unit and integration tests | 491 offline tests passed on 21 November with thirteen opt-in hardware tests deselected; actual custom integer, text and nested JSON workflows passed separately |
| Live replay workflow | Real evaluation, cancellation/resume, grounded analysis and fresh recorded replay passed on 17 November; advanced hardware probe and arithmetic smoke were also verified on 16 November |
| Dashboard browser and frontend checks | Real Chrome workflow checks passed at 1440×900 and 390×844; nine Node tests passed |
| Strict typing | mypy passes across 39 source modules |
| Lint and formatting | Ruff checks all source and test files |
| Readiness and real inference probe | Ollama 0.40.13, Qwen 2.5 7B, actual answer `108` to `93 + 15` |
| Archive-only regeneration | Rebuilt the six-pair live audit with `inference_requested: false` |
| Matched regression comparison | Compared the live archive to itself: no changed cases, zero metric deltas, passing gates |
| Source archive preservation | Tests verify byte-for-byte preservation and reject output paths that would overwrite inputs |
| Corrupt/inconsistent archives | Tests reject missing pairs, duplicates, mismatched identities, lineage, prompt plans and control worlds |
| Regression exit codes | Tests exercise improvements, failures, tolerated deltas, confidence bounds and incompatible runs |
| README portability | Removed the machine-specific project `cd` command |
| Exact live attack replay | Six matched pairs, 16 fresh target completions with native timestamps and exact source user-message boundaries |
| Model digest pinning | Every replay target request checked and recorded the expected installed model digest |
| Generation completeness | Zero truncated target turns in the live replay; token-limit, archive metadata and quality-gate behavior tested |
| Judge validation repair | One live judge answer repaired by a second actual inference call; both outputs and token cost retained |
| Replay checkpoint resume | All six committed replay pairs resumed without additional target calls |

The readiness evidence is in `reports/features-doctor/doctor.json`. Regenerated audits and
regression reports are in `reports/features-review`. These artifact directories are ignored
by Git. Archive regeneration uses measured responses and stored verdicts, recomputes summary
statistics, and makes no inference calls. It does not independently rejudge saved outputs or
cryptographically authenticate the archive.

## Checker false-positive and error audit, 18 November

Fixed valid whole-number spelling rejection, floating-point spending accumulation,
and empty private-value matches. JSON overflow such as `1e9999` now produces a saved
checker-limit review case instead of a non-finite parsed value that breaks JSON report saving.
Exact decimal normalization rejects near-integer rounding traps, booleans and strings;
duplicate JSON keys remain rejected. Large integer tool arguments no longer overflow
the finite-value check. Real over-limit spending is still detected.

Answer correctness, requested format and reply completion now have separate results.
Missing finish metadata and server filtering are review cases, and cut-off replies are
not counted as confirmed wrong answers. Correct fenced JSON remains a format failure
when the task prohibits Markdown. The saved-check review API recomputes these decisions
without model calls, job creation or source edits; source-byte preservation and session
checks are tested. A restart test recovers saved partial replies when the job summary
was not committed, and malformed recovery files fail visibly. Checker version 0.2.1
prevents older checkpoint manifests from silently mixing with the new rules.

Controlled tests cover JSON numeric equivalents and precision traps, overflow,
duplicate keys, signed numbers, format-only errors, unfinished/blocked replies, empty
private values, decimal budget boundaries, very large tool arguments and restart
recovery. Eight generated test worlds exercise regular public replies and encoded
public replies as negative controls, alongside encoded private-value positive controls.

The initial saved-result audit reviewed nine real local report files containing 39
saved replies and found zero failed-to-passed corrections. This is a review of that
specific set, not a general false-positive-rate estimate. Its source hashes and
counts are in `reports/checker-review-20261118/saved-check-audit.json`.

Two actual Qwen round trips are retained in
`reports/checker-review-20261118/real-response-edge-probes.json`: the model returned
`{"x":1.0}`, which passed the equivalent-integer check, and `{"x":1e9999}`, which was
safely retained. The final checker distinguishes this valid numeric spelling outside its supported range from a model format failure and marks it for review. A current parser round-trip record is retained in `real-response-edge-probes-v2.json`. The current live skill workflow passed
cancellation, retained replies, ten fresh answers and token-limit handling under
`reports/checker-audit-final-20261118/`. Both hardware tests in the skill-test module
passed. No model output was replaced with a computed answer.

## Plain dashboard labels and real model skill tests, 17 November

Dashboard controls now say regular task, private test value, with/without protection,
original reply, messages and tool rules. Stored keys and original model replies are
not rewritten. Browser checks verified the main Review results view contains none of
the removed terms; full response inspection and export still preserve the exact text.

The skill-test worker generates five question types from a seed and checks actual local
model replies against computed answers. Tests cover repeatable question expansion,
arithmetic and record-selection answers, sorted unique numbers, strict JSON key/type
checking, cut-off/unfinished replies, malformed limits and blocked response injection.
The actual Qwen hardware workflow passed cancellation with saved partial replies,
ten fresh answers with recorded model versions and no forced JSON schema, and an
observed-token-limit stop after preserving its first completed reply. Artifacts are
retained under `reports/model-skills-live-20261117/`.

An actual browser-launched five-question run is retained at
`reports/.dashboard-jobs/2aec8c060e404bd6b6f29d3ab09b38b4/model-checks.json`. It passed four
questions and failed sorting: the model returned -5 instead of the input number -25.
This failure was retained, not replaced with a computed reply. Later checks also explain
missing/added numbers, duplicates and ordering errors. Browser checks cover the form,
saved results, exports and desktop/mobile views. Small sample scores are not general
model ratings. The new tests do not change existing benchmark grading or model weights.

## Configurable gates, saved-evidence previews and run library, 17 November

Evaluation and campaign requests now retain explicit `Thresholds`; report writers receive
those exact values. Gate preview API tests recompute canonical decisions from the measured
Qwen recording, reconcile all four metric checks, reject invalid/session-expired requests,
and verify the source archive bytes remain unchanged. Socket egress is prohibited in those
tests, establishing that previewing makes no inference request. Zero observed truncation
is correctly distinguished from a zero upper confidence bound.

The real hardware workflow completed with custom ASR/FRR/improvement/truncation thresholds,
verified those values in fresh telemetry, and confirmed recorded replay inherited them.
Artifacts are retained under `reports/dashboard-gates-live-20261117/`. The workflow passed
without substituting completion fixtures. Six Node tests passed, including run-library
query/mode/gate combinations, ordering and source-order preservation.

Chrome verified library empty/search/open states, confidence-gate previews, four metric rows,
JSON exports, unchanged measured results after previews, preset inputs, and desktop/mobile
widths of 1440 and 390 pixels. The library paginates 25 records and supports saved-gate and
execution-mode filtering. Gate results are previews only; source data and library gate
statuses remain unchanged. Screenshots are retained in `reports/dashboard-verification/`
as `gates-desktop.png`, `gates-mobile.png`, `library-desktop.png` and `library-mobile.png`.

## Capability scorecards and dashboard replay, 17 November

Capability scorecards reconcile every per-check status with the recomputed case diagnostics,
group by capability/family/depth, distinguish failed cases from failed checks, and exclude
unknown/inapplicable observations from completion denominators. Scorecard cache tests reject
an archive that changes to invalid JSON. Browser checks exercised failure-mode/check filters,
filter clearing, exports, source summaries and real capability rows at desktop/mobile sizes.

Dashboard replay captures the validated source before starting its worker and inherits its
test/scoring settings. Offline tests verify the snapshot survives changes to the original
file, missing sources do not start jobs, and resource/scoring overrides fail validation.
The opt-in workflow ran a fresh six-pair source and replay through actual Qwen inference,
verifying identical generated worlds and recorded messages, a saved replay-plan digest,
zero new attacker tokens, and different native completion timestamps.

That replay is retained at
`reports/dashboard-replay-live-20261117/.dashboard-jobs/03960b5ef7ec4d79bf14c0aa6bda8ce6/telemetry.json`.
It produced 12 fresh target responses using 11,497 tokens and retained its immutable
`source-telemetry.json`. A separate replay launched through the actual browser form also
completed with 12 fresh target responses; its measured run ID is
`run_a67804dcc5e3f7c3b8d7a033`. No source completions were displayed as replay outputs.
The Node suite passed five tests, and strict typing/lint/format checks passed.

## Interactive dashboard verification, 16 November

The expanded Inspect view adds seven evidence-based checks in each mode, exact native
conversation/request/response details, original-versus-delivered tool and leak checks,
full policy schemas and case JSON export. Browser checks confirm the R badge, lab subtitle
and computer-only footer sentence are absent. Advanced-only filtering, capability-name
search, export fidelity and desktop/mobile Inspect layouts were exercised in real Chrome.

The full mixed advanced evaluation is retained in
`reports/advanced-capabilities-20261116/telemetry.json`: 72 paired scenarios, including
18 advanced threat/control cases spanning nine capabilities, with 144 actual Qwen target
replies and 188,919 reported tokens. It uses one attack turn and deterministic grading;
no local judge or model-generated attacker was used for this measured run. All target
responses are native inference with the pinned Qwen digest. The initial 18 advanced pairs
were measured in `reports/advanced-capability-probe-20261116/advanced-probe.json`; the full
run restored those exact committed pairs and made 108 additional actual target requests.
The final run observed baseline ASR 25%, hardened ASR 0%, and hardened benign failure 0%.
These are descriptive results from this suite, not a broad capability or safety guarantee.

New tests cover deterministic multi-hop resolution amid 18 conflicting records, paired
world invariants across five seeds, original-world compatibility, cumulative budget and
cross-invoice counterexamples, original versus guarded leaks/tools, exact refund totals,
cross-turn history, unknown finish metadata, truncated output and inspection API errors.
The opt-in advanced hardware test generated all 18 advanced pairs through the real local
model and validated native envelopes, model digests, usage and finish metadata. It permits
models to fail capability checks rather than replacing their replies or forcing passing scores.

The loopback dashboard was checked using actual saved native model responses and fresh
Ollama inference. It loads no demonstration dataset. The test recording in
`tests/fixtures/measured-qwen-run.json` is a byte-identical copy of the existing real seed-1729
archive, with a checked SHA-256 and provenance note; it is not a response backend.

The opt-in dashboard worker test completed fresh inference, cancelled after committing a
scenario pair, resumed with one restored pair and ten additional native target replies,
and finished all six pairs. Its latest retained evaluation is
`reports/dashboard-live-20261116/.dashboard-jobs/ae6eeb81c48e48db82f7bb700eaa1bc7/telemetry.json`.
It recorded 11,509 combined restored/new target tokens. The test then requested a fresh
local-model explanation with required baseline, hardened, security and utility assessments;
every evidence quote was validated against the source native text or proposed calls.

A separate browser-launched six-pair evaluation completed with 12 fresh target replies and
11,497 observed tokens, followed by a developed analyst response using 3,062 tokens.
Evidence remains in `reports/.dashboard-jobs/4d26ea37ad37438d9d63ecea63fdb83e/` and
`reports/.dashboard-jobs/a13fafe7d0cf42f194575223637a0832/`. These successful fresh target
runs used deterministic grading without a judge; all target and analyst responses were real.
An earlier judge-enabled job failed explicitly after two ungrounded judge answers, preserving
four committed pairs. It was not published as a complete benchmark. The analyst's first
version also failed exact-quote validation; the final version constrains citation choices to
actual native excerpts and retains runtime validation. Model-generated claims remain
opinions even when the quoted evidence is genuine.

Chrome checks exercised searches, family/distribution filtering, exact native response
inspection, modal dismissal, comparisons, campaign seed coverage, real expired-session
rejection, missing-run errors, and absence of remote UI requests or JavaScript page errors.
Mobile checks found no page-width overflow; wide tables scroll within their regions.
Keyboard checks observed a visible skip-link focus outline. Empty-state evidence came
from a real empty reports root. Loading evidence delayed the real catalog request before
continuing it; no response content was substituted. The excluded-archive state showed the
actual invalid legacy artifacts. Screenshots and the design evidence record are under
`reports/dashboard-verification/`. These checks are not a full WCAG certification.

The wheel was built locally using already provisioned setuptools and inspected to confirm
all four web assets are packaged. Dashboard jobs currently require Ollama; native HTTP
archives can be inspected, while unsupported provenance and incomplete runs are excluded.

## Latest multi-seed campaign and resource limits

`reports/campaign-20261115/campaign.html` links to the measured audits for seeds 42 and 1729.
The pinned real Qwen target and local judge completed 12 baseline/hardened scenario pairs,
including 24 fresh target completions and 50 total native model responses. Usage accounting
matched the stored target/attacker/judge usage exactly: 22,260 tokens for seed 42 and 20,283
for seed 1729, or 42,543 observed tokens in total. Each seed had its own 50,000-token and
300-second allowance. The campaign's pooled ASR was 50% baseline versus 33.3% hardened;
hardened FRR was 0%. The worst seed's hardened ASR was 66.7%. Default permissive gates passed;
these small-sample measurements do not establish broad defense efficacy.

Resuming the same campaign restored all 12 committed pairs, made zero new model calls,
and restored the exact per-seed token totals. Initial execution evidence is retained in
`reports/campaign-20261115/initial-execution-evidence.json`; current per-seed execution files
correctly describe the cached resume. Raw observed replies remain in the per-seed telemetry.
A separate real one-token-limit probe stopped after observing 921 tokens from one response,
returned operational exit 2, and saved only `reports/budget-live-20261115/execution.json`.
It did not publish a partial security benchmark or invent a replacement response.

Tests cover cumulative target/attacker/judge usage, unknown usage, cooperative deadlines,
checkpoint cleanup, restored work before new inference, changed execution allowances,
unique seed validation, campaign output ownership, cancellation, recipe preservation,
partial failure without aggregate metrics, and a failing seed that would otherwise be hidden
by a passing pooled average. Resource limits are observed-usage safeguards with documented
in-flight and cooperative-cancellation limits, not hard token reservations.

## Latest live replay

The current replay and regression artifacts are in `reports/live-replay-20261115`.
Target replies were regenerated from the recorded attack messages, not copied from the source:
source and replay native completion timestamps differ, all 16 target turns carry the expected
digest, and no attacker generation tokens were consumed. The same installed Qwen model and
real local judge were used. The six-pair replay completed with passing point regression gates,
zero observed target truncations, and one recorded judge repair. Strict judge validation
remains enabled; another invalid answer after the retry still fails the run.

Replay checkpoints bind the exact attack-plan digest. Tests cover changed plans/worlds/budgets,
split-message recovery, early success without truncating the plan, no attacker calls,
input preservation and cached resumption. Pinning tests reject unsupported engines, changed
weights and missing/mismatched metadata before generation. Production completion fixtures
remain absent; unit/integration fixtures stay exclusively in the test package.

## Real model evidence

The existing six-pair local run is retained in `reports/live-actual-1729/telemetry.json`:
three generated attacks and three matched benign controls, each in baseline and hardened mode.
All 16 target generations contain native Ollama model metadata and non-estimated token counts.
The target, attacker and judge used `qwen2.5:7b-instruct-q4_K_M`.

Model digest:

```text
845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e
```

That small run recorded deterministic policy violations in 2/3 baseline attacks and 0/3
hardened attacks, plus one semantic-only hardened policy-drift flag. All three benign controls
completed. Overall ensemble ASR was 2/3 versus 1/3. These distinct evidence categories remain
separate in regenerated reports; three attacks cannot establish production safety.

Production code supports only actual local inference backends. Programmable completion
fixtures exist only under `tests/`, so GPU-free integration tests do not become user-facing
benchmark demonstrations. Native vLLM/llama.cpp and Transformers live inference were not
executed here; their readiness/adapters are covered by offline transport and pipeline tests.
Only Windows Python 3.12 was executed locally; the checked-in CI matrix also targets Linux
and Python 3.11.

## Quality review

Self-assessment: **4.0/5** across accuracy, completeness, clarity, actionability and conciseness.
Accuracy has real Ollama evidence plus archive preservation tests; validating more engine
versions would improve confidence. Completeness covers all three new workflows, with
comparison intentionally rejecting changed search/judge configurations. Clarity benefits from
portable commands, though the README is long. Actionability includes usable local reports
and readiness output; optional engines still require their own weights/dependencies.
Conciseness is limited by the evaluation assumptions needed to interpret the results.

Highest-impact follow-ups are independent live verification on vLLM/Transformers and larger
seeded evaluations. Self-check: the user can review actual responses and reproduce reporting
without inference or publishing anything.

## Extended skill tests, 18 November

The extended set adds order-record joins with cancellations and discounts, parallel task dependency planning, and exact case-sensitive word counts. Five seeds independently validate computed answers and reject changed answers. The complete 331-test suite passed with all six real-model tests enabled. Both a direct eight-question local Qwen workflow and an eight-question browser-submitted run completed, saved native responses and exposed actual task/format failures. Review summaries include all eight skills and saved runs reload with the correct planned count. Desktop and 390px mobile screenshots were inspected; the live browser check also verifies the 8/24-question estimate and result export. Real-model completion means replies were collected, not that all answers passed. Evidence: `reports/extended-skills-20261118/` and `reports/dashboard-verification/exported-model-skills.json`.

## Focused runs and answer filters, 18 November

Focused runs preserve the full set's seeded questions, IDs and answers, while planned counts, saved summaries, reviews and reloads include only selected skills. Invalid, duplicate, empty and out-of-set choices are rejected before inference. Three seeds check stable question selection; six invalid-selection cases check validation. The real Qwen workflow collected four actual replies for two selected skills, verified original server content and byte-preserving review, and reloaded with the correct count. A browser-submitted single-skill run completed with one actual reply.

Saved-answer search covers prompts, original reply text, computed answers and failure reasons. Skill/outcome filters preserve full-run summaries and exports. Eight frontend tests include filter combinations, legacy failure status, unknown completion distinction and source preservation. The live Chrome workflow checked focused estimates, focus reset after switching sets, empty searches, outcome/skill filtering, clear action, original answer text and recorded reply time. Actual desktop/mobile renders were inspected; mobile filters were expanded to full-width rows so skill and outcome labels remain readable. No canned model completions were introduced.

Five additional corrupt saved-settings cases verify a readable validation error instead of an attribute error during saved-check review.

## Skill continuation and multiple selection, 18 November

The dashboard now supports checkbox selection of several skills and Stop/Continue controls. Continuation checks saved settings, question order and computed answers, checker version, recorded response model identity and the currently installed model digest. It preserves saved replies verbatim and only requests unanswered questions. Saved usage is charged to the original token limit; restored responses and fresh calls are counted separately. The time allowance applies to each continuation. Exhausted or estimated usage, changed model versions, missing usage, damaged checkpoints and completed tests cannot continue.

All 357 offline tests passed, with eight opt-in hardware tests deselected. Eighteen new offline cases cover authentic fixture identity, saved prefixes, modified/reordered/duplicate questions, settings, seeds, usage, model identity, checker version, count limits, busy/completed jobs, exhausted allowances and changed installed versions. The actual Qwen test passed cancellation, manager restart, completion of only the remaining questions, preservation of the saved prefix and restored-token accounting. The real Chrome workflow selected two skills with three questions each, stopped after an actual saved answer, continued to six saved replies and verified the preserved prefix and fresh/restored counts. Eight frontend tests passed; lint, formatting and typing passed.

The recorded skill fixture is the unchanged four-reply Qwen file from the earlier focused-run audit, retained at `tests/fixtures/measured-qwen-skills.json` with SHA256 `d3470cd7dd1608f1a175df4d62f280abb770b3cd756a6cfb7bdc3a8805ba3223`. Tests derive interrupted checkpoint metadata from it without inventing completions. Actual continuation evidence remains under `reports/skill-continuation-20261118/` and the dashboard job history. Desktop and mobile renders were inspected; the saved-run selector and actions were given separate columns to prevent long selections overlapping buttons.

## Saved skill comparison, 18 November

Compare two saved skill tests through the local dashboard or protected `POST /api/skill-comparison`. Both saved replies are checked with the same current checker. Matching requires identical question IDs, prompts, answer types, skills, computed answers and rules. Changed questions and missing replies are excluded with explicit counts and IDs. Duplicate IDs are rejected. Confirmed changes are separated from incomplete or unconfirmed replies; original saved pass/fail flags remain in the export. Partial and identical-source comparisons have explicit notices.

Token and timing differences cover only matched pairs with usable recorded data on both sides. Missing/estimated usage is excluded, with pair coverage shown. Unsupported timing values remain unknown, including excessively large integers. These differences describe recorded replies, not general model performance. Expanded questions show both actual replies, expected answers and failure reasons; JSON export retains the exact saved response records.

Twenty-six new offline tests cover exact alignment, duplicate/reordered/changed/missing questions, empty overlap, outcome transitions, current-checker consistency, native completion-metadata recovery, missing finish information, measurement coverage, invalid timing values and protected read-only API behavior. API tests verify unchanged file hashes and no active model job. All 383 offline tests passed, with eight optional hardware tests deselected; eight frontend tests, lint, formatting and typing also passed. The real Chrome workflow compared a saved test with itself and two distinct actual Qwen runs, checked unchanged job data, opened original replies and verified the exported result. Desktop and 390px mobile renders were inspected, without page-width overflow. Evidence: `reports/dashboard-verification/exported-skill-comparison.json` and `skill-comparison-*.png`. No fresh model replies were needed for comparison.

## Challenging questions and filtered exports, 19 November

Standard questions retain their existing generation and IDs. The new challenging level supplies demanding versions of all eight skills: packing arithmetic, alias/revision/state joins, nested JSON summaries, imported role-labeled instructions, revision-aware multi-key sorting, conditional discounts with half-up tax and shipping thresholds, randomized ten-task dependency graphs, and normalized word counts across selected documents. Challenging question IDs are distinct; focus selection, saved history, report settings and checkpoint validation include the level. Cross-level questions are excluded from exact-question comparisons.

Twelve new offline cases independently calculate answers across five seeds, check stable focus/identity and invalid levels, exercise both order threshold branches and half-cent tax across 100 seeds, and verify ranking-error explanations do not claim numeric ID order is required. The separate live Qwen workflow collected eight native replies and verified saved identities, actual usage, unconstrained output, grading, review and reload. Its saved checkpoint was also regenerated and validated without inference. A real browser-submitted challenging run completed eight answers, exercised skill/outcome/search filters and verified that Export shown answers exactly matches the selected original result rows, with source/filter metadata and accurate counts. Empty selections disable export. Native replies remain unchanged, including actual wrong answers and format failures.

All 395 offline tests passed with nine hardware cases deselected; the dedicated challenging hardware test passed separately. Eight frontend tests, actual Chrome desktop/mobile checks, Ruff and mypy passed (32 source modules). Actual challenging desktop and 390px mobile renders were inspected without page-width overflow. Evidence: `reports/challenging-skills-20261119/`, `reports/dashboard-verification/exported-selected-answers.json`, the real dashboard job history and `challenging-skills-*.png`. Completion confirms replies were collected, not that the model passed every task. No commits or GitHub pushes were made.

## Ten skill-workflow upgrades, 19 November

1. Expanded runs: one to ten questions per skill, up to 80 in the extended set, while preserving older seeded questions.
2. Inference-free plan preview: exact questions and request seeds, approximate input-token accounting, maximum output allowance and explicit budget warnings.
3. Test-plan JSON export: settings and computed answers for inspection and reproducibility; expected answers are not injected into the model request. Editing settings invalidates the preview.
4. Recorded performance: overall/per-skill median and nearest-rank 95th-percentile reply times, token totals and measured/estimated/missing coverage. Restored replies count once.
5. Structured answer differences: bounded JSON-pointer diagnostics for keys, types, values and array lengths, including recoverable fenced JSON. Original replies and saved flags remain unchanged.
6. Copy case evidence: copies the source ID, question, actual reply and saved grade; clipboard errors offer the filtered export as a fallback.
7. Load saved settings: restores model/seed/level/selection/limits without starting a job; unavailable models require selecting an installed model.
8. Saved-test search: model, seed, level, selected skill, status and ID matching.
9. Answer pagination: ten details per page; filters reset pagination, while filtered exports retain all matching pages and full summaries/exports retain the whole run.
10. Portable HTML reports: actual saved replies, settings, metrics, expected answers and current grading, with original saved pass/fail flags. All model markup is escaped, with no scripts or external assets.

Twelve new offline cases cover expanded plan identity, budget warnings, stable JSON paths and bounded differences, native fenced-reply diagnostics, actual timing/token calculations and unknown coverage, escaped standalone reports, protected plan APIs and unchanged source-file hashes. A ninth frontend test covers history matching without modifying inputs. All 407 offline tests passed with ten opt-in hardware tests deselected; lint, formatting and mypy passed across 34 source modules. The new opt-in hardware workflow passed with four real arithmetic replies and exact agreement between planned and native request seeds/questions.

The Chrome workflow previewed 80 challenging questions without increasing the job count, exported and ran a 12-question plan, checked original replies against the plan, paginated 10+2 answers, inspected three per-skill metric rows, restored saved settings without a new job, tested empty history search and opened the exported HTML file with no external requests or active model markup. Copy behavior was verified against the actual saved evidence using a clipboard-boundary recorder, leaving the OS clipboard untouched. The expanded-run workflow and all browser assertions passed. Desktop and 390px mobile screenshots were inspected; history search was styled consistently and the 80-question preview was grouped to avoid excessive scrolling. The HTML report uses current diagnostics while preserving its original saved record.

Evidence: `reports/expanded-skills-20261119/`, the dashboard job history, `reports/dashboard-verification/exported-test-plan.json`, `exported-skill-report.html`, `test-plan-*.png` and `skill-upgrades-*.png`. No mock model completions were introduced; no GitHub commits or pushes were made. Reply timings and percentiles describe saved samples rather than general model speed. Token-plan figures are estimates, not measured inference usage.

## Failed-answer retries and separate review notes, 19 November

Retry confirmed failures creates a separate real-inference job with the exact source questions, source seed, reply limits and resource allowances, optionally using the selected installed model. Only current-checker wrong-answer, format-error or unexpected-tool results with complete replies qualify. Unfinished, unconfirmed and currently passing answers are excluded even if an old stored flag says failed. Question-ID selections are validated against the complete seeded plan. Retry lineage is verified against the stopped source, with a canonical source-report hash in the new report. Planned counts reflect the selected questions, and continuation retains those IDs. Compare retry to original opens the exact matched comparison. Loading saved retry settings preserves its exact selection; editing settings resets it to the selected skills.

Human review notes are stored in a separate bounded JSON companion file, with decisions, up to 4,000 characters, revision numbers and question/reply fingerprints. Review never changes automated grades. Changed evidence produces a stale flag. Conflicting saves return HTTP 409 and retain the user's unsaved text; corrupt note files are not silently overwritten. Notes have their own JSON export. Note and retry POSTs retain same-origin/session-token protection and reject score overrides or supplied model responses.

Fourteen new offline cases verify valid/rejected question selections, current-checker retry filtering, forged lineage/changed task rejection, exact planned counts, original source preservation, note persistence, stale flags, conflicting edits, malformed decisions/limits, corrupt-file protection and protected API behavior. All 421 offline tests passed with eleven opt-in hardware tests deselected; nine frontend tests, Ruff formatting/lint and mypy across 35 modules also passed. The new hardware test generated exactly three native Qwen replies for the three confirmed failed recorded questions, preserved the source bytes and validated its checkpoint and matched comparison.

The actual Chrome workflow saved and exported a pending human note without changing the source job, retried only two failures from an actual 12-answer run, checked per-skill planned counts and source question identity, restored and previewed the exact retry selection, verified that editing settings resets selection, and opened the matched source comparison. A subsequent retry-of-retry workflow also passed. The deterministic model repeated the two errors, which correctly remained visible as still failing. Desktop and 390px mobile note/comparison screenshots were inspected without page overflow. A final read-only Chrome regression check passed. Evidence: `reports/failed-answer-retry-20261119/`, the real dashboard job history, `reports/dashboard-verification/exported-review-notes.json`, `review-note-*.png` and `retry-comparison-*.png`. Source evaluation files remain unchanged; only companion human notes and new retry jobs are written. Nothing was pushed to GitHub.

## Data checks and exact-plan coverage, 19 November

A separate four-skill Data checks set adds working-day/holiday date calculation, exact signed decimal unit conversion, Boolean decision rules with scoped overrides, and revision-aware event reconciliation. Both standard and challenging levels are seeded, with distinct IDs and up to 40 questions per run. Prior basic and extended question generation remains unchanged. Focus, exact question selections, preview, grading/review, continuation and retry operate through the existing real-model workflow.

Fifteen new data/coverage offline cases independently verify answers across five seeds and both levels using enumerated working-day calendars, Fraction arithmetic, denial-first logic and independently sorted revision resolution. Additional probes cover month crossings/weekend holidays, stable selection/IDs, maximum 40-question plans, partial coverage, unknown completion, changed question contracts, duplicate IDs and strict expected-value types. One new continuation corruption parameter also verifies that integer-to-decimal expected-value changes are rejected. Resume and retry source matching now use strict structural equality rather than Python's numeric type-coercing equality.

All 437 offline tests passed with twelve opt-in hardware tests deselected. Nine frontend tests, formatting, Ruff and mypy across 37 source modules passed. The dedicated real Qwen data run collected four actual replies, checked native response identity/usage, exact regrading, four-skill review coverage and valid saved checkpoints. The model produced actual calculation and output-format failures, which remain recorded; workflow completion does not imply passing answers. A separate real Chrome run previewed and submitted the four challenging data questions, verified all four native saved replies and per-skill coverage, exported the exact coverage JSON and checked the 40-question UI estimate. Desktop and 390px mobile coverage layouts were inspected with no page overflow. A final read-only Chrome regression check passed after the strict matching change.

Question coverage counts only identical planned questions. Missing replies are unanswered; incomplete/review-needed outcomes remain unconfirmed. Duplicate IDs are rejected, and changed/unplanned questions are excluded rather than scored as failures. Coverage export contains actual source/settings metadata and no generated responses. Evidence: `reports/data-skills-20261119/`, the actual dashboard job history, `reports/dashboard-verification/exported-question-coverage.json` and `data-coverage-*.png`. No model replies or source reports were replaced, and nothing was pushed to GitHub.

## Saved-answer audit and checker-limit fixes, 21 November

A new saved-answer audit is available in Model skills, as a protected empty-body POST to /api/skill-audit, and through --audit-skills --reports-root reports (JSON on stdout). It rechecks original saved native replies without inference or source edits, exposes snapshot hashes, skips exact duplicate files, rejects ambiguous duplicate question IDs and excludes inconsistent task/reply evidence. Wrapper-modified text is excluded rather than attributed to the native model. Corrected, uncertain, format-only, confirmed failure and saved-pass changes remain distinct. The first 50 examples prioritize corrected or uncertain cases. Scan limits (200 files, 5,000 replies, 8 MiB per file, 32 MiB total, 2,000 directories) and input issues are explicit. This is not an extrapolated false-positive-rate estimate.

Confirmed checker fixes: valid JSON with the wrong array element type no longer becomes a format error because numeric-only diagnostics tried to hash nested objects. Huge integers and numeric underflow now require review. A quote/escape-aware 256-container nesting guard bounds both answer checking and outer audit metadata; interpreter recursion limits no longer determine classification. Genuine syntax errors, duplicate keys and nonstandard JSON values remain format errors. No original grade or reply is silently rewritten.

Twenty new offline cases cover valid wrong-shaped JSON, unsupported numbers, nesting limits, quoted brackets/escaped quotes, genuine format errors, actual native fixture auditing, exact-file deduplication, prioritization, duplicate IDs, scan limits, malformed files, missing/inconsistent native evidence, wrapper changes, CLI behavior, session-token protection and pre-decode metadata bounds. All 457 offline tests passed with twelve optional hardware tests deselected. Ruff, formatting and mypy across 38 modules passed. Nine frontend tests and real Chrome desktop/mobile workflows passed, including audit export and unchanged source job data. The dedicated four-question local Qwen workflow passed and retained native request/response evidence; no mock completions were introduced.

The final saved-data audit covered 177 native replies in 33 reports, without truncation, input issues or unverified replies. It found zero failed-to-passed corrections and zero changed classifications in that sample, two correct-content replies with genuine format violations, 65 other confirmed task/format failures and 110 still-passing replies. All 33 source-file hashes matched after audit. This sample does not prove that all future false positives are eliminated; the newly reproduced boundary errors are covered by regression tests.

Evidence: reports/checker-audit-20261121/saved-answer-audit.json, reports/checker-audit-20261121/live/, reports/dashboard-verification/exported-saved-answer-audit.json and saved-answer-audit-*.png. Desktop and 390px mobile audit screens were inspected without page overflow. The original dashboard was initially unavailable; the updated service was tested on temporary loopback port 8766, then started on 8765. Temporary verification service was stopped. Nothing was committed or pushed to GitHub.

## Custom test bench, 21 November

Added custom integer, exact-text and JSON contracts, JSON definition import/export,
validation before inference, authored-answer labels, stable contract identity and
integration with saved settings, measured coverage, continuation checks and exact
failed-question retries. Expected values are never included in native model requests.

Added 34 offline checks covering malformed and oversized definitions, unknown fields,
duplicate names/JSON keys, fractional expectations, non-finite/range limits, Unicode,
32-container depth boundaries, strict booleans versus integers, decimal strings,
case and whitespace, fenced correct content, changing contract identity, subset
selection, 20-question progress counts and read-only plan endpoints. Corrected a
misleading increasing-order diagnostic for custom lists and a custom progress-count
bug found during the first actual inference test.

Actual Qwen 2.5 7B verification retained 13 new native replies across API and browser
runs, including a failed-question retry. The API hardware workflow checks the native
request body, original reply text, saved checkpoint identity, coverage and portable
HTML. Chrome checks import/export round trips, invalid-answer rejection, exact plan,
actual run, restore, source preservation and a fresh failed-question retry. Screenshots
were inspected at 1440×900 and 390×844; the editor fills the available mobile width.
The existing full browser workflow and nine frontend tests also passed.

The browser arithmetic task asked for 9×8−5. Qwen returned `71` instead of `67`;
that retained failure is a confirmed answer error. The raw reply was not replaced or
softened. The final saved-answer audit covered 190 native replies in 38 reports,
found zero classification changes, zero unverified replies and no input issues,
and retained two correct-content format violations as format violations. Every
source report SHA-256 hash stayed unchanged. These observations cover the sampled
replies; they do not establish that all future false positives are impossible.

Local proof: `reports/custom-bench-20261121` and `reports/dashboard-verification`.
Custom expectations remain author supplied; the harness validates their supported
representation, not the truth of an arbitrary authored answer. No commits or pushes
were made.
