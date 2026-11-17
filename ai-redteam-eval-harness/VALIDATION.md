# Validation record

Validated locally on 17 November 2026 with Python 3.12.14. Changes remain local; no commits
or pushes were made during this feature extension.

## Current verification

| Check | Evidence |
|---|---|
| Unit and integration tests | 277 offline tests passed on 17 November; four opt-in hardware tests excluded from that offline run |
| Live replay workflow | Real evaluation, cancellation/resume, grounded analysis and fresh recorded replay passed on 17 November; advanced hardware probe and arithmetic smoke were also verified on 16 November |
| Dashboard browser and frontend checks | Real Chrome workflow checks passed at 1440×900 and 390×844; four Node tests passed |
| Strict typing | mypy passes across 30 source modules |
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
