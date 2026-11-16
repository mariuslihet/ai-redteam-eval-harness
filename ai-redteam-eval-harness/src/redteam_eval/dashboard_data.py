"""Read measured archives for the dashboard; never supply demonstration completions."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from redteam_eval.archive import RunArchive, load_archive
from redteam_eval.campaign import CampaignRecord
from redteam_eval.reporting import gate, summarize


def artifact_id(relative: str, kind: str = "run") -> str:
    return kind + "_" + hashlib.sha256(relative.encode()).hexdigest()[:24]


def native_evidence(archive: RunArchive) -> int:
    """Require retained native response envelopes for every displayed target turn.

    This is provenance validation, not cryptographic authentication of a local JSON file.
    Legacy fixtures and simulated-provider envelopes cannot satisfy this contract.
    """
    turns = 0
    if any(profile.name != "direct" for profile in archive.config.providers):
        raise ValueError("Only direct native-inference archives are shown in the dashboard")
    for pair in archive.results:
        for trace in (pair.baseline, pair.hardened):
            for turn in trace.turns:
                native = turn.response.response_envelope
                model = native.get("model")
                if not isinstance(model, str) or not model or model == "local-simulated":
                    raise ValueError(
                        "Missing native model identity; archive is not displayed as measured inference"
                    )
                if archive.config.backend.kind == "ollama":
                    message = native.get("message")
                    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                        raise ValueError("Missing native Ollama response envelope")
                elif archive.config.backend.kind == "openai":
                    if not isinstance(native.get("choices"), list) or not native["choices"]:
                        raise ValueError("Missing native OpenAI-compatible response envelope")
                else:
                    raise ValueError(
                        "This dashboard requires retained native HTTP inference envelopes"
                    )
                turns += 1
    return turns


class ReportStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, tuple[int, int, RunArchive, dict[str, Any]]] = {}
        self._paths: dict[str, Path] = {}
        self._lock = threading.RLock()

    def bounded_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or path.is_symlink():
            raise ValueError("Artifact escapes the configured report directory")
        return resolved

    def _run(self, path: Path) -> tuple[RunArchive, dict[str, Any]]:
        path = self.bounded_path(path)
        stat = path.stat()
        if stat.st_size > 64 * 1024 * 1024:
            raise ValueError("Archive exceeds the dashboard's 64 MiB inspection limit")
        key = str(path)
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2], cached[3]
            archive = load_archive(path)
            native_evidence(archive)
            summary = summarize(archive.results, archive.config.seed)
            if len(self._cache) >= 128:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (stat.st_mtime_ns, stat.st_size, archive, summary)
            return archive, summary

    def catalog(self) -> dict[str, Any]:
        runs: list[dict[str, Any]] = []
        campaigns: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        paths: dict[str, Path] = {}
        candidates = sorted(
            [*self.root.rglob("telemetry.json"), *self.root.rglob("campaign.json")],
            key=lambda p: p.as_posix(),
        )
        for candidate in candidates[:1000]:
            relative = candidate.relative_to(self.root).as_posix()
            try:
                path = self.bounded_path(candidate)
                if candidate.name == "telemetry.json":
                    archive, summary = self._run(path)
                    identifier = artifact_id(relative)
                    runs.append(
                        {
                            "id": identifier,
                            "label": candidate.parent.name,
                            "relative_path": relative,
                            "model": archive.target_model,
                            "seed": archive.config.seed,
                            "modified_ns": path.stat().st_mtime_ns,
                            "summary": summary,
                            "gate_failures": gate(summary, archive.thresholds),
                            "execution_mode": archive.execution_mode or "adaptive",
                        }
                    )
                else:
                    if path.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError("Campaign index exceeds the inspection limit")
                    record = CampaignRecord.model_validate_json(path.read_text(encoding="utf-8"))
                    if len(record.seeds) < 2 or len(set(record.seeds)) != len(record.seeds):
                        raise ValueError("Campaign must declare at least two distinct seeds")
                    completed_seeds = [item.seed for item in record.seed_results]
                    if len(set(completed_seeds)) != len(completed_seeds) or not set(
                        completed_seeds
                    ).issubset(record.seeds):
                        raise ValueError("Campaign contains duplicate or undeclared measured seeds")
                    identifier = artifact_id(relative, "campaign")
                    seed_items: list[dict[str, Any]] = []
                    measured = []
                    for item in record.seed_results:
                        source = self.bounded_path(
                            path.parent / item.report_directory / "telemetry.json"
                        )
                        archive, summary = self._run(source)
                        if (
                            archive.config.seed != item.seed
                            or archive.target_model != record.config.backend.model
                        ):
                            raise ValueError("Campaign seed metadata differs from measured run")
                        measured.extend(archive.results)
                        seed_items.append(
                            {
                                "seed": item.seed,
                                "run_id": artifact_id(source.relative_to(self.root).as_posix()),
                                "summary": summary,
                                "gate_failures": gate(summary, record.thresholds),
                            }
                        )
                    complete = (
                        record.status == "complete"
                        and {i["seed"] for i in seed_items} == set(record.seeds)
                        and len(seed_items) == len(record.seeds)
                    )
                    campaign_summary = summarize(measured, record.config.seed) if complete else None
                    failures = (
                        ["pooled: " + f for f in gate(campaign_summary, record.thresholds)]
                        if campaign_summary
                        else []
                    )
                    failures.extend(
                        f"seed {i['seed']}: {f}" for i in seed_items for f in i["gate_failures"]
                    )
                    campaigns.append(
                        {
                            "id": identifier,
                            "label": candidate.parent.name,
                            "relative_path": relative,
                            "model": record.config.backend.model,
                            "modified_ns": path.stat().st_mtime_ns,
                            "status": "complete"
                            if complete
                            else record.status
                            if record.status != "complete"
                            else "partial",
                            "seeds": record.seeds,
                            "seed_results": seed_items,
                            "summary": campaign_summary,
                            "gate_failures": failures,
                            "error": record.error,
                        }
                    )
                paths[identifier] = candidate
            except (ValueError, OSError, KeyError, TypeError) as exc:
                reason = (
                    "Archive validation failed" if isinstance(exc, ValidationError) else str(exc)
                )
                issues.append({"relative_path": relative, "reason": reason[:300]})
        if len(candidates) > 1000:
            issues.append(
                {"relative_path": ".", "reason": "Catalog inspection capped at 1000 artifacts"}
            )
        with self._lock:
            self._paths = paths
        return {
            "runs": sorted(runs, key=lambda item: item["modified_ns"], reverse=True),
            "campaigns": sorted(campaigns, key=lambda item: item["modified_ns"], reverse=True),
            "issues": issues,
            "status": "partial" if issues else "ready",
        }

    def path(self, identifier: str) -> Path:
        with self._lock:
            path = self._paths.get(identifier)
        if path is None:
            self.catalog()
            with self._lock:
                path = self._paths.get(identifier)
        if path is None:
            raise KeyError("Measured artifact not found")
        return self.bounded_path(path)

    def detail(self, identifier: str) -> dict[str, Any]:
        path = self.path(identifier)
        if path.name != "telemetry.json":
            raise ValueError("Select an individual measured run for case inspection")
        archive, summary = self._run(path)
        result = archive.model_dump(mode="json")
        result.update(
            id=identifier,
            summary=summary,
            gate_failures=gate(summary, archive.thresholds),
            relative_path=path.relative_to(self.root).as_posix(),
        )
        return result

    def archive(self, identifier: str) -> RunArchive:
        path = self.path(identifier)
        if path.name != "telemetry.json":
            raise ValueError("Case analysis requires a measured run")
        return self._run(path)[0]

    def download(self, identifier: str, filename: str) -> bytes:
        if filename not in {
            "telemetry.json",
            "turns.csv",
            "review.jsonl",
            "audit.md",
            "regression.json",
        }:
            raise ValueError("This artifact is not available for download")
        path = self.bounded_path(self.path(identifier).parent / filename)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Download exceeds the inspection limit")
        return path.read_bytes()
