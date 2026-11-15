"""Sequential multi-seed campaigns using real Runner inference and per-seed checkpoints."""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from redteam_eval.budgets import ExecutionRecord
from redteam_eval.checkpoint import atomic_json
from redteam_eval.models import Model, PairResult, RunConfig, Thresholds
from redteam_eval.reporting import gate, summarize, write_reports
from redteam_eval.runner import Runner


class CampaignSpec(Model):
    seeds: list[int] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def distinct_seeds(self) -> CampaignSpec:
        if len(set(self.seeds)) != len(self.seeds) or any(
            seed < 0 or seed > 2**63 - 1 for seed in self.seeds
        ):
            raise ValueError("Campaign seeds must be distinct integers from 0 through 2**63-1")
        return self


class SeedRecord(Model):
    seed: int
    report_directory: str
    summary: dict[str, Any]
    gate_failures: list[str]
    execution: ExecutionRecord


class CampaignRecord(Model):
    format_version: Literal[1] = 1
    type: Literal["multi_seed_campaign"] = "multi_seed_campaign"
    status: Literal["running", "complete", "failed"] = "running"
    config: RunConfig
    seeds: list[int]
    thresholds: Thresholds
    seed_results: list[SeedRecord] = Field(default_factory=list)
    summary: dict[str, Any] | None = None
    worst_seed_hardened_asr: float | None = None
    gate_failures: list[str] = Field(default_factory=list)
    error: str | None = None


def write_campaign(directory: Path, record: CampaignRecord) -> None:
    atomic_json(directory / "campaign.json", record.model_dump(mode="json"))
    lines = [
        "# Multi-seed security campaign",
        "",
        f"Status: {record.status}.",
        f"Target: {record.config.backend.model}.",
        f"Completed seeds: {len(record.seed_results)}/{len(record.seeds)}.",
        "",
        "| Seed | Baseline ASR | Hardened ASR | Hardened FRR | Seed gate |",
        "|---|---:|---:|---:|---|",
    ]
    for item in record.seed_results:
        lines.append(
            f"| {item.seed} | {item.summary['baseline']['asr']['rate']:.1%} | "
            f"{item.summary['hardened']['asr']['rate']:.1%} | "
            f"{item.summary['hardened']['frr']['rate']:.1%} | "
            f"{'FAIL' if item.gate_failures else 'PASS'} |"
        )
    if record.summary is not None:
        lines.extend(
            [
                "",
                f"Pooled hardened ASR: {record.summary['hardened']['asr']['rate']:.1%}.",
                f"Pooled hardened FRR: {record.summary['hardened']['frr']['rate']:.1%}.",
                f"Worst seed hardened ASR: {record.worst_seed_hardened_asr:.1%}.",
                f"Campaign gate: {'FAIL' if record.gate_failures else 'PASS'}.",
                "",
                "Pooled intervals are descriptive; scenario-cluster bootstrap is retained. "
                "Every seed must pass its own gate as well as the pooled gate.",
            ]
        )
    else:
        lines.extend(["", "Campaign is incomplete. No aggregate benchmark result is published."])
    if record.error:
        lines.extend(["", f"Error: {record.error}"])
    lines.extend(["", *[f"- {failure}" for failure in record.gate_failures]])
    markdown = "\n".join(lines) + "\n"
    (directory / "campaign.md").write_text(markdown, encoding="utf-8")
    links = "".join(
        f'<li><a href="seed-{item.seed}/audit.html">Observed responses for seed {item.seed}</a></li>'
        for item in record.seed_results
    )
    (directory / "campaign.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\"><title>Multi-seed evaluation</title>"
        "<style>body{font:16px system-ui;max-width:1000px;margin:32px auto;padding:20px}"
        "pre{white-space:pre-wrap;line-height:1.6}</style><body><pre>"
        + html.escape(markdown)
        + "</pre><h2>Per-seed real-model audits</h2><ul>"
        + links
        + "</ul></body></html>",
        encoding="utf-8",
    )


async def run_campaign(
    config: RunConfig,
    spec: CampaignSpec,
    thresholds: Thresholds,
    output: Path,
    checkpoint: Path | None = None,
) -> CampaignRecord:
    if config.replay_plan_digest:
        raise ValueError("A multi-seed campaign cannot use one recorded replay plan")
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".campaign.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as exc:
        raise ValueError(
            "Campaign output is locked; verify the prior process stopped before removing .campaign.lock"
        ) from exc
    record = CampaignRecord(config=config, seeds=spec.seeds, thresholds=thresholds)
    all_results: list[PairResult] = []
    started = False
    try:
        recipe = {
            "config": config.model_dump(
                mode="json", exclude={"max_total_tokens", "max_run_seconds"}
            ),
            "seeds": spec.seeds,
        }
        fingerprint = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
        manifest = output / "campaign-manifest.json"
        if manifest.exists():
            if json.loads(manifest.read_text(encoding="utf-8"))["fingerprint"] != fingerprint:
                raise ValueError(
                    "Campaign output belongs to a different model/configuration/seed recipe; use a new directory"
                )
        else:
            atomic_json(manifest, {"fingerprint": fingerprint, **recipe})
        started = True
        write_campaign(output, record)
        for seed in spec.seeds:
            seed_config = config.model_copy(update={"seed": seed}, deep=True)
            seed_output = output / f"seed-{seed}"
            seed_output.mkdir(parents=True, exist_ok=True)
            runner = Runner(seed_config)
            try:
                results = await runner.run(checkpoint / f"seed-{seed}" if checkpoint else None)
            finally:
                atomic_json(
                    seed_output / "execution.json", runner.budget.snapshot().model_dump(mode="json")
                )
            summary, failures = write_reports(seed_output, results, seed_config, thresholds)
            record.seed_results.append(
                SeedRecord(
                    seed=seed,
                    report_directory=f"seed-{seed}",
                    summary=summary,
                    gate_failures=failures,
                    execution=runner.budget.snapshot(),
                )
            )
            all_results.extend(results)
            write_campaign(output, record)
        record.summary = summarize(all_results, config.seed)
        record.worst_seed_hardened_asr = max(
            item.summary["hardened"]["asr"]["rate"] for item in record.seed_results
        )
        record.gate_failures = ["pooled: " + f for f in gate(record.summary, thresholds)]
        record.gate_failures.extend(
            f"seed {item.seed}: {failure}"
            for item in record.seed_results
            for failure in item.gate_failures
        )
        record.status = "complete"
        write_campaign(output, record)
        return record
    except BaseException as exc:
        if started:
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
            write_campaign(output, record)
        raise
    finally:
        lock.unlink(missing_ok=True)
