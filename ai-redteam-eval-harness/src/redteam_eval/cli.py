"""Rich CLI; status 0 = pass, 1 = security gate failed, 2 = operational failure."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table

from redteam_eval.models import BackendConfig, ProviderProfile, RunConfig, Thresholds
from redteam_eval.reporting import write_reports
from redteam_eval.runner import Runner


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Offline procedural local-LLM security benchmark")
    p.add_argument("--config", type=Path, help="RunConfig JSON; cannot combine with run overrides")
    p.add_argument("--backend", choices=("ollama", "openai", "transformers"))
    p.add_argument("--model")
    p.add_argument("--endpoint")
    p.add_argument("--seed", type=int)
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
    p.add_argument("--max-asr", type=float, default=1)
    p.add_argument("--max-frr", type=float, default=1)
    p.add_argument("--min-defense-delta", type=float, default=-1)
    p.add_argument("--confidence-gate", action="store_true", help="Gate on CI bounds")
    p.add_argument("--verbose", action="store_true")
    return p


def build_config(args: argparse.Namespace) -> RunConfig:
    run_keys = (
        "backend",
        "model",
        "endpoint",
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
    if args.config:
        if (
            any(getattr(args, k) is not None for k in run_keys)
            or args.algorithmic_attacker
            or args.no_judge
            or args.require_judge
        ):
            raise ValueError("--config cannot be combined with run overrides")
        return RunConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    backend = BackendConfig(
        kind=args.backend or "ollama",
        model=args.model or "qwen2.5:7b-instruct-q4_K_M",
        endpoint=args.endpoint
        or ("http://127.0.0.1:8080" if args.backend == "openai" else "http://127.0.0.1:11434"),
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
        config.attacker = backend.model_copy(update={"model": args.attacker_model or backend.model})
    if not args.no_judge:
        config.judge = backend.model_copy(update={"model": args.judge_model or backend.model})
    config.require_judge = args.require_judge
    return RunConfig.model_validate(config.model_dump())


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    console = Console(stderr=True)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    try:
        config = build_config(args)
        thresholds = Thresholds(
            max_asr=args.max_asr,
            max_frr=args.max_frr,
            min_defense_delta=args.min_defense_delta,
            use_upper_ci=args.confidence_gate,
        )
        results = asyncio.run(Runner(config).run(args.checkpoint))
        summary, failures = write_reports(args.output, results, config, thresholds)
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
