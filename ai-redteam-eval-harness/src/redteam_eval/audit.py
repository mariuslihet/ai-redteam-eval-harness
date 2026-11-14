"""Portable review queues and per-turn measurements derived from complete paired results."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Literal

from redteam_eval.models import Model, Outcome, PairResult


class ReviewCase(Model):
    pair_key: str
    scenario_id: str
    provider: str
    mode: Literal["baseline", "hardened"]
    turn_id: str
    depth: int
    family: str
    benign: bool
    document_format: str
    mutation: str
    reasons: list[str]
    outcome: Outcome
    findings: list[str]
    semantic_rationale: str | None
    judge_error: str | None
    guard_events: list[str]


def review_cases(results: list[PairResult]) -> list[ReviewCase]:
    """Select explicit evidence, ensemble disagreement and unfinished benign tasks for review."""
    cases: list[ReviewCase] = []
    for pair in results:
        for trace in (pair.baseline, pair.hardened):
            for turn in trace.turns:
                reasons = []
                if turn.verdict.findings:
                    reasons.append("deterministic_finding")
                if turn.verdict.disagreement:
                    reasons.append("ensemble_disagreement")
                if turn.verdict.judge_error:
                    reasons.append("judge_error")
                if turn.verdict.false_refusal:
                    reasons.append("benign_task_failure")
                if turn.verdict.attack_success and not turn.verdict.findings:
                    reasons.append("semantic_attack_success")
                if turn.response.guard_events:
                    reasons.append("defense_intervention")
                if not reasons:
                    continue
                cases.append(
                    ReviewCase(
                        pair_key=pair.key,
                        scenario_id=pair.scenario.id,
                        provider=trace.provider,
                        mode=trace.mode,
                        turn_id=turn.id,
                        depth=turn.depth,
                        family=trace.family,
                        benign=trace.benign,
                        document_format=pair.scenario.metadata.get("document_format", "unknown"),
                        mutation=turn.mutation,
                        reasons=reasons,
                        outcome=turn.verdict.outcome,
                        findings=turn.verdict.findings,
                        semantic_rationale=turn.verdict.semantic.rationale
                        if turn.verdict.semantic
                        else None,
                        judge_error=turn.verdict.judge_error,
                        guard_events=turn.response.guard_events,
                    )
                )
    return cases


def csv_text(value: str) -> str:
    """Prevent text identifiers becoming spreadsheet formulas on CSV import."""
    return (
        "'" + value
        if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
        else value
    )


def write_audit_exports(directory: Path, results: list[PairResult]) -> int:
    """Emit stable JSONL review references and a spreadsheet-friendly row for every turn."""
    cases = review_cases(results)
    with (directory / "review.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for case in cases:
            stream.write(case.model_dump_json() + "\n")
    columns = [
        "pair_key",
        "scenario_id",
        "provider",
        "mode",
        "family",
        "benign",
        "document_format",
        "turn_id",
        "depth",
        "mutation",
        "outcome",
        "attack_success",
        "false_refusal",
        "disagreement",
        "finding_count",
        "guard_event_count",
        "input_tokens",
        "output_tokens",
        "estimated_tokens",
        "target_latency_ms",
        "attacker_tokens",
        "judge_tokens",
        "attempts",
    ]
    with (directory / "turns.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for pair in results:
            for trace in (pair.baseline, pair.hardened):
                for turn in trace.turns:
                    row: dict[str, str | int | float | bool] = {
                        "pair_key": pair.key,
                        "scenario_id": pair.scenario.id,
                        "provider": trace.provider,
                        "mode": trace.mode,
                        "family": trace.family,
                        "benign": trace.benign,
                        "document_format": pair.scenario.metadata.get("document_format", "unknown"),
                        "turn_id": turn.id,
                        "depth": turn.depth,
                        "mutation": turn.mutation,
                        "outcome": turn.verdict.outcome,
                        "attack_success": turn.verdict.attack_success,
                        "false_refusal": turn.verdict.false_refusal,
                        "disagreement": turn.verdict.disagreement,
                        "finding_count": len(turn.verdict.findings),
                        "guard_event_count": len(turn.response.guard_events),
                        "input_tokens": turn.response.usage.input_tokens,
                        "output_tokens": turn.response.usage.output_tokens,
                        "estimated_tokens": turn.response.usage.estimated,
                        "target_latency_ms": turn.response.latency_ms,
                        "attacker_tokens": turn.attacker_usage.input_tokens
                        + turn.attacker_usage.output_tokens,
                        "judge_tokens": turn.judge_usage.input_tokens
                        + turn.judge_usage.output_tokens,
                        "attempts": turn.response.attempts,
                    }
                    writer.writerow(
                        {k: csv_text(v) if isinstance(v, str) else v for k, v in row.items()}
                    )
    return len(cases)
