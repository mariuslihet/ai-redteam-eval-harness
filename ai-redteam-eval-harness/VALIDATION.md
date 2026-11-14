# Validation record

Validated locally on 14 November 2026 with Python 3.12.14. Changes remain local; no commits
or pushes were made during this feature extension.

## Current verification

| Check | Evidence |
|---|---|
| Unit, integration and live smoke tests | 134 passed, including live inference against the installed Qwen model |
| Strict typing | mypy passes across 20 source modules |
| Lint and formatting | Ruff checks all source and test files |
| Readiness and real inference probe | Ollama 0.40.13, Qwen 2.5 7B, actual answer `108` to `93 + 15` |
| Archive-only regeneration | Rebuilt the six-pair live audit with `inference_requested: false` |
| Matched regression comparison | Compared the live archive to itself: no changed cases, zero metric deltas, passing gates |
| Source archive preservation | Tests verify byte-for-byte preservation and reject output paths that would overwrite inputs |
| Corrupt/inconsistent archives | Tests reject missing pairs, duplicates, mismatched identities, lineage, prompt plans and control worlds |
| Regression exit codes | Tests exercise improvements, failures, tolerated deltas, confidence bounds and incompatible runs |
| README portability | Removed the machine-specific project `cd` command |

The readiness evidence is in `reports/features-doctor/doctor.json`. Regenerated audits and
regression reports are in `reports/features-review`. These artifact directories are ignored
by Git. Archive regeneration uses measured responses and stored verdicts, recomputes summary
statistics, and makes no inference calls. It does not independently rejudge saved outputs or
cryptographically authenticate the archive.

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
