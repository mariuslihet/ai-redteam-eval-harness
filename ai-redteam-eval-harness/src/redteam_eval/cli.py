"""Rich CLI; status 0 = pass, 1 = security gate failed, 2 = operational failure."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

from redteam_eval.archive import RunArchive, load_archive
from redteam_eval.campaign import CampaignSpec, run_campaign
from redteam_eval.checkpoint import atomic_json
from redteam_eval.doctor import diagnose
from redteam_eval.models import BackendConfig, ProviderProfile, RunConfig, Thresholds
from redteam_eval.regression import (
    RegressionLimits,
    compare_runs,
    compatible_configs,
    write_regression,
)
from redteam_eval.replay import plan_digest
from redteam_eval.reporting import gate, summarize, write_reports
from redteam_eval.runner import Runner


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Offline procedural local-LLM security benchmark")
    p.add_argument("--config", type=Path, help="RunConfig JSON; cannot combine with run overrides")
    source = p.add_mutually_exclusive_group()
    source.add_argument(
        "--report-from",
        type=Path,
        help="Regenerate reports from measured telemetry without inference",
    )
    source.add_argument(
        "--doctor", action="store_true", help="Check configured local engine and model readiness"
    )
    source.add_argument(
        "--replay-from",
        type=Path,
        help="Replay recorded attack messages with fresh real target/judge inference",
    )
    p.add_argument(
        "--probe",
        action="store_true",
        help="With --doctor, execute a real arithmetic inference probe",
    )
    p.add_argument(
        "--compare-to", type=Path, help="Compare against a complete previous telemetry archive"
    )
    p.add_argument("--compare-mode", choices=("baseline", "hardened"), default="hardened")
    p.add_argument("--max-asr-increase", type=float, default=0)
    p.add_argument("--max-frr-increase", type=float, default=0)
    p.add_argument("--max-violation-increase", type=float, default=0)
    p.add_argument("--backend", choices=("ollama", "openai", "transformers"))
    p.add_argument("--model")
    p.add_argument("--endpoint")
    p.add_argument("--model-digest", help="Expected installed Ollama model SHA-256 digest")
    p.add_argument("--seed", type=int)
    p.add_argument(
        "--seeds", nargs="+", type=int, help="Run a campaign across distinct seeds, sequentially"
    )
    p.add_argument(
        "--max-total-tokens",
        type=int,
        help="Observed token allowance per run, including target/attacker/judge and restored pairs",
    )
    p.add_argument(
        "--max-run-seconds", type=float, help="Cooperative wall-time allowance per run/seed"
    )
    p.add_argument("--cases-per-family", type=int)
    p.add_argument("--max-turns", type=int)
    p.add_argument("--concurrency", type=int)
    p.add_argument("--retries", type=int)
    p.add_argument("--max-tokens", type=int)
    p.add_argument(
        "--providers", nargs="+", choices=("direct", "openai", "anthropic", "azure", "bedrock")
    )
    p.add_argument("--jitter-ms", type=float)
    p.add_argument("--rate-limit-probability", type=float)
    p.add_argument("--attacker-model", help="Local model using the target backend kind/endpoint")
    p.add_argument("--judge-model", help="Local model using the target backend kind/endpoint")
    p.add_argument("--algorithmic-attacker", action="store_true", help="Disable LLM refinement")
    p.add_argument("--no-judge", action="store_true", help="Use deterministic grading only")
    p.add_argument("--require-judge", action="store_true")
    p.add_argument("--checkpoint", type=Path)
    p.add_argument("--output", type=Path, default=Path("reports/latest"))
    p.add_argument("--max-asr", type=float)
    p.add_argument("--max-frr", type=float)
    p.add_argument("--min-defense-delta", type=float)
    p.add_argument("--max-truncation-rate", type=float)
    p.add_argument(
        "--confidence-gate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Gate on CI bounds; archive reports preserve their saved setting unless overridden",
    )
    p.add_argument("--verbose", action="store_true")
    return p


RUN_KEYS = (
    "backend",
    "model",
    "endpoint",
    "model_digest",
    "seed",
    "cases_per_family",
    "max_turns",
    "concurrency",
    "retries",
    "max_tokens",
    "providers",
    "jitter_ms",
    "rate_limit_probability",
    "attacker_model",
    "judge_model",
)


def has_run_overrides(args: argparse.Namespace) -> bool:
    return (
        any(getattr(args, k) is not None for k in RUN_KEYS)
        or args.algorithmic_attacker
        or args.no_judge
        or args.require_judge
    )


def build_config(args: argparse.Namespace) -> RunConfig:
    if args.config:
        if has_run_overrides(args):
            raise ValueError("--config cannot be combined with run overrides")
        return RunConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    backend = BackendConfig(
        kind=args.backend or "ollama",
        model=args.model or "qwen2.5:7b-instruct-q4_K_M",
        endpoint=args.endpoint
        or ("http://127.0.0.1:8080" if args.backend == "openai" else "http://127.0.0.1:11434"),
        expected_digest=args.model_digest,
    )
    values = {
        k: getattr(args, k)
        for k in ("seed", "cases_per_family", "max_turns", "concurrency", "retries", "max_tokens")
        if getattr(args, k) is not None
    }
    config = RunConfig.model_validate(
        {
            **values,
            "backend": backend.model_dump(),
            "providers": [
                ProviderProfile(
                    name=name,
                    jitter_ms=args.jitter_ms or 0,
                    rate_limit_probability=args.rate_limit_probability or 0,
                ).model_dump()
                for name in (args.providers or ["direct"])
            ],
        }
    )
    if not args.algorithmic_attacker:
        red_model = args.attacker_model or backend.model
        config.attacker = backend.model_copy(
            update={
                "model": red_model,
                "expected_digest": backend.expected_digest if red_model == backend.model else None,
            }
        )
    if not args.no_judge:
        judge_model = args.judge_model or backend.model
        config.judge = backend.model_copy(
            update={
                "model": judge_model,
                "expected_digest": backend.expected_digest
                if judge_model == backend.model
                else None,
            }
        )
    config.require_judge = args.require_judge
    return RunConfig.model_validate(config.model_dump())


def replay_config(args: argparse.Namespace, recorded: RunArchive) -> RunConfig:
    if args.config:
        config = build_config(args)
    else:
        allowed = {"backend", "model", "endpoint", "model_digest", "concurrency", "retries"}
        if any(getattr(args, name) is not None for name in RUN_KEYS if name not in allowed) or (
            args.algorithmic_attacker or args.no_judge or args.require_judge
        ):
            raise ValueError(
                "Replay inherits the saved suite, attack/token budgets and judge; use a full --config for other settings"
            )
        config = recorded.config.model_copy(deep=True)
        backend = config.backend.model_dump()
        changed_model = args.model is not None and args.model != config.backend.model
        changed_kind = args.backend is not None and args.backend != config.backend.kind
        if changed_model or changed_kind:
            backend["expected_digest"] = None
        for flag, field in (
            ("backend", "kind"),
            ("model", "model"),
            ("endpoint", "endpoint"),
            ("model_digest", "expected_digest"),
        ):
            if getattr(args, flag) is not None:
                backend[field] = getattr(args, flag)
        config.backend = BackendConfig.model_validate(backend)
        for name in ("concurrency", "retries"):
            if getattr(args, name) is not None:
                setattr(config, name, getattr(args, name))
    config.replay_plan_digest = plan_digest(recorded)
    return config


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    console = Console(stderr=True)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    try:
        if args.probe and not args.doctor:
            raise ValueError("--probe requires --doctor")
        if args.doctor and (args.checkpoint or args.compare_to):
            raise ValueError("--doctor cannot use --checkpoint or --compare-to")
        if args.seeds and (
            args.seed is not None
            or args.report_from
            or args.replay_from
            or args.doctor
            or args.compare_to
        ):
            raise ValueError(
                "--seeds cannot combine with --seed, archive/replay, doctor or run comparison modes"
            )
        if (args.report_from or args.doctor) and (
            args.max_total_tokens is not None or args.max_run_seconds is not None
        ):
            raise ValueError(
                "Execution allowances apply to inference runs, not saved-report or readiness operations"
            )
        source_archive = None
        replay_archive = load_archive(args.replay_from) if args.replay_from else None
        if args.report_from:
            if args.config or args.checkpoint or has_run_overrides(args):
                raise ValueError(
                    "--report-from cannot use configuration, run overrides or checkpoints"
                )
            source_archive = load_archive(args.report_from)
            config = source_archive.config
        elif replay_archive:
            config = replay_config(args, replay_archive)
        else:
            config = build_config(args)
        for name in ("max_total_tokens", "max_run_seconds"):
            if getattr(args, name) is not None:
                setattr(config, name, getattr(args, name))
        if config.replay_plan_digest and not (source_archive or replay_archive):
            raise ValueError(
                "A replay-configured run requires --replay-from with the recorded archive"
            )
        if args.doctor:
            diagnosis = asyncio.run(diagnose(config.backend, probe=args.probe, seed=config.seed))
            args.output.mkdir(parents=True, exist_ok=True)
            atomic_json(args.output / "doctor.json", diagnosis.model_dump(mode="json"))
            console.print(
                "Local model ready" if diagnosis.healthy else "Local model needs attention"
            )
            for issue in diagnosis.issues:
                console.print(issue, markup=False)
            print(diagnosis.model_dump_json())
            return 0 if diagnosis.healthy else 1
        for source_path in (args.report_from, args.compare_to, args.replay_from):
            if source_path and source_path.resolve() == (args.output / "telemetry.json").resolve():
                raise ValueError(
                    "Choose a separate output directory to preserve the input telemetry archive"
                )
        previous = load_archive(args.compare_to) if args.compare_to else None
        if previous:
            compatible_configs(previous.config, config)
        saved_thresholds = source_archive.thresholds if source_archive else Thresholds()
        threshold_values = saved_thresholds.model_dump()
        for name in ("max_asr", "max_frr", "min_defense_delta", "max_truncation_rate"):
            if getattr(args, name) is not None:
                threshold_values[name] = getattr(args, name)
        if args.confidence_gate is not None:
            threshold_values["use_upper_ci"] = args.confidence_gate
        thresholds = Thresholds.model_validate(threshold_values)
        regression_limits = RegressionLimits(
            max_asr_increase=args.max_asr_increase,
            max_frr_increase=args.max_frr_increase,
            max_violation_increase=args.max_violation_increase,
            confidence_gate=thresholds.use_upper_ci,
        )
        if previous is None and (
            args.max_asr_increase
            or args.max_frr_increase
            or args.max_violation_increase
            or args.compare_mode != "hardened"
        ):
            raise ValueError("Regression limits and --compare-mode require --compare-to")
        if args.seeds:
            record = asyncio.run(
                run_campaign(
                    config, CampaignSpec(seeds=args.seeds), thresholds, args.output, args.checkpoint
                )
            )
            console.print(f"Campaign reports: {args.output.resolve()}")
            for failure in record.gate_failures:
                console.print(f"Gate failed: {failure}", markup=False)
            print(
                json.dumps(
                    {
                        "status": "fail" if record.gate_failures else "pass",
                        "seeds": record.seeds,
                        "completed_seeds": len(record.seed_results),
                        "inference_requested": True,
                    }
                )
            )
            return 1 if record.gate_failures else 0
        if source_archive:
            results = source_archive.results
        else:
            runner = Runner(config)
            try:
                results = asyncio.run(runner.run(args.checkpoint, replay=replay_archive))
            finally:
                args.output.mkdir(parents=True, exist_ok=True)
                atomic_json(
                    args.output / "execution.json", runner.budget.snapshot().model_dump(mode="json")
                )
        comparison = None
        if previous:
            # Validate compatibility before overwriting any output artifacts.
            candidate_summary = summarize(results, config.seed)

            candidate = RunArchive(
                config=config,
                results=results,
                summary=candidate_summary,
                thresholds=thresholds,
                gate_failures=gate(candidate_summary, thresholds),
                inference_backend=config.backend.kind,
                target_model=config.backend.model,
            )
            comparison = compare_runs(previous, candidate, regression_limits, args.compare_mode)
        summary, failures = write_reports(args.output, results, config, thresholds)
        if comparison:
            write_regression(args.output, comparison)
            failures = failures + ["regression: " + item for item in comparison["gate_failures"]]
        table = Table(title="Paired local security evaluation")
        for column in ("Mode", "ASR", "FRR", "Target tokens"):
            table.add_column(column)
        for mode in ("baseline", "hardened"):
            data = summary[mode]
            table.add_row(
                mode,
                f"{data['asr']['rate']:.1%}",
                f"{data['frr']['rate']:.1%}",
                str(data["target_tokens"]),
            )
        console.print(table)
        console.print(f"Reports: {args.output.resolve()}")
        for failure in failures:
            console.print(f"[red]Gate failed: {failure}[/red]")
        print(
            json.dumps(
                {
                    "status": "fail" if failures else "pass",
                    "pairs": len(results),
                    "defense_delta": summary["defense_delta"]["delta"],
                    "inference_requested": source_archive is None,
                    "comparison": comparison is not None,
                    "attack_replay": config.replay_plan_digest is not None,
                }
            )
        )
        return 1 if failures else 0
    except Exception as exc:

        def describe(error: BaseException) -> str:
            if isinstance(error, BaseExceptionGroup):
                return "; ".join(describe(child) for child in error.exceptions[:5])
            return f"{type(error).__name__}: {error}"

        console.print(f"Evaluation failed: {describe(exc)}", markup=False)
        if args.verbose:
            console.print_exception()
        return 2
