# Validation record

Validated locally on 15 November 2026 with Python 3.12.14. Changes remain local; no commits
or pushes were made during this feature extension.

## Current verification

| Check | Evidence |
|---|---|
| Unit, integration and live smoke tests | 178 passed, including live inference against the installed Qwen model |
| Strict typing | mypy passes across 23 source modules |
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
