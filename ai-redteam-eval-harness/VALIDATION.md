# Validation record

Executed on 14 November 2026 in this workspace using the provisioned Python 3.12.14 environment.

| Check | Observed result |
|---|---|
| Unit and integration suite | 87 passed, 1 skipped |
| Strict mypy | No issues in 16 source files |
| Ruff lint and formatting | Clean |
| Dependency consistency | `pip check`: no broken requirements |
| Full offline example | 144 baseline/hardened pairs across four provider profiles |
| Security gate example | Passed hardened ASR <=0, FRR <=1, defense delta >=1 |
| Checkpoint replay | Identical telemetry SHA-256 before and after resume |
| Live local-model smoke | Skipped; no running local inference server or Ollama executable found |

The demonstration uses the explicit deterministic test double. Its baseline ASR is 100%,
hardened ASR is 0%, and both FRRs are 100%. Those are fixture behavior, not model-security
measurements: the default CI double refuses the first turn and leaks a synthetic key on
the second turn. It intentionally does not solve benign controls. Programmable callbacks
and scripts test successful benign completion, tool behavior and semantic judge decisions
separately. The permissive demonstration FRR gate is deliberate, not a recommended production
threshold.

Telemetry checksum after the recorded demonstration and its resumed run:

```text
961f63b5a1decac6264d9a5d7b688cebbe67d1c2a919afef65f9c7a2312e3381
```

Artifacts are generated under `reports/demo`; checkpoint files are under `checkpoints/demo`.
Both directories are ignored by Git. `README.md` supplies live-model and offline commands.
The checked-in CI configuration targets Python 3.11 and 3.12 on Linux and Windows; only the
local Python 3.12 Windows execution was performed here.

## Quality review

Overall self-assessment: **4.0/5**; the code is reviewable and the offline workflow is verified,
while production and live-hardware claims remain limited by the evidence above.

| Axis | Score | Evidence and next improvement |
|---|---:|---|
| Accuracy | 4/5 | Offline adapter parsing and policies pass tests; real engine and model behavior still need a live run. |
| Completeness | 4/5 | All requested subsystem boundaries are implemented; profiles approximate non-streaming vendor APIs, and execution trees use parent-linked linear trajectories. Validate against installed engines and captured provider contracts before broader claims. |
| Clarity | 4/5 | README explains pairing, metrics and limitations; the long configuration guide could become separate focused guides as the project grows. |
| Actionability | 4/5 | Environment, CLI, reports, checkpoints and CI are ready; live evaluation requires an available local inference engine. |
| Conciseness | 4/5 | Sixteen source modules keep subsystem boundaries explicit; documentation is lengthy because evaluation assumptions materially affect interpretation. |

Highest-impact remaining validation: run the marked smoke test and a real paired experiment
using the documented local model; then compare provider codecs against version-pinned wire
fixtures if exact service conformance becomes a deployment requirement.

Self-check: the user should be able to reproduce the offline results immediately and see
precisely why they cannot yet be treated as a live-model security benchmark.
