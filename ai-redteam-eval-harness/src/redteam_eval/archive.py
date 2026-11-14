"""Validate saved telemetry before offline report regeneration or regression analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from redteam_eval.models import Family, Model, PairResult, RunConfig, Scenario, Thresholds


class RunArchive(Model):
    """Stored measurements are authoritative; cached summary statistics are recomputed."""

    format_version: Literal[1] = 1
    package_version: str = "unknown"
    config: RunConfig
    summary: dict[str, Any]
    thresholds: Thresholds
    gate_failures: list[str]
    inference_backend: str
    target_model: str
    results: list[PairResult] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_consistent_run(self) -> RunArchive:
        if (
            self.inference_backend != self.config.backend.kind
            or self.target_model != self.config.backend.model
        ):
            raise ValueError("Archive model identity does not match its configuration")
        expected = len(Family) * self.config.cases_per_family * 2 * len(self.config.providers)
        if len(self.results) != expected:
            raise ValueError(
                f"Incomplete archive: expected {expected} pairs, found {len(self.results)}"
            )
        profiles = {p.name for p in self.config.providers}
        keys: set[str] = set()
        scenario_profiles: dict[str, set[str]] = {}
        scenario_values: dict[str, Scenario] = {}
        matched_worlds: dict[str, dict[bool, Scenario]] = {}
        families: dict[tuple[Family, bool], set[str]] = {}
        for pair in self.results:
            scenario = pair.scenario
            if pair.key in keys:
                raise ValueError(f"Duplicate archive pair: {pair.key}")
            keys.add(pair.key)
            provider = pair.baseline.provider
            if provider not in profiles or pair.key != f"{scenario.id}:{provider}":
                raise ValueError(f"Archive pair identity mismatch: {pair.key}")
            scenario_profiles.setdefault(scenario.id, set()).add(provider)
            if scenario.id in scenario_values and scenario_values[scenario.id] != scenario:
                raise ValueError("A scenario differs across provider profiles")
            scenario_values[scenario.id] = scenario
            world = matched_worlds.setdefault(scenario.pair_id, {})
            if scenario.benign in world and world[scenario.benign].id != scenario.id:
                raise ValueError("Multiple scenarios claim the same threat/control identity")
            world[scenario.benign] = scenario
            families.setdefault((scenario.family, scenario.benign), set()).add(scenario.id)
            for mode, trace in (("baseline", pair.baseline), ("hardened", pair.hardened)):
                if (
                    trace.mode != mode
                    or trace.provider != provider
                    or trace.scenario_id != scenario.id
                    or trace.benign != scenario.benign
                    or trace.family != scenario.family
                ):
                    raise ValueError(f"Archive trace identity mismatch: {pair.key}/{mode}")
                limit = 1 if scenario.benign else self.config.max_turns
                if not trace.turns or len(trace.turns) > limit:
                    raise ValueError("Archive trace has an invalid turn count")
                parent: str | None = None
                turn_ids: set[str] = set()
                for index, turn in enumerate(trace.turns, 1):
                    if turn.depth != index or turn.parent_id != parent or turn.id in turn_ids:
                        raise ValueError("Archive turn lineage is inconsistent")
                    parent = turn.id
                    turn_ids.add(turn.id)
            if [t.prompt for t in pair.baseline.turns] != [t.prompt for t in pair.hardened.turns]:
                raise ValueError("Baseline and hardened attack plans do not match")
        if any(value != profiles for value in scenario_profiles.values()):
            raise ValueError("Archive is missing a provider for a generated scenario")
        for world in matched_worlds.values():
            if set(world) != {False, True}:
                raise ValueError("Archive is missing a matched threat or benign control")
            excluded = {"id", "benign", "prompt", "document"}
            if world[False].model_dump(exclude=excluded) != world[True].model_dump(
                exclude=excluded
            ):
                raise ValueError("Threat and benign control do not share the same generated world")
        for family in Family:
            for benign in (False, True):
                if len(families.get((family, benign), set())) != self.config.cases_per_family:
                    raise ValueError("Archive family/control distribution is incomplete")
        return self


def load_archive(path: Path) -> RunArchive:
    """Read an existing measured run. Never load a model, fabricate responses or infer anew."""
    return RunArchive.model_validate_json(path.read_text(encoding="utf-8"))
