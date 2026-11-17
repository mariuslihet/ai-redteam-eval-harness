"""Capability rollups from recomputed case checks, without additional inference."""

from collections import Counter
from typing import Any

from redteam_eval.archive import RunArchive
from redteam_eval.inspection import inspect_pair


def capability_scorecard(archive: RunArchive) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    cases = []
    for pair in archive.results:
        inspection = inspect_pair(pair)
        family = str(pair.scenario.family)
        level = inspection["challenge_level"]
        capability = pair.scenario.metadata.get("capability", family.replace("_", " ") + " · core")
        group = groups.setdefault(
            (family, level, capability),
            {
                "family": family,
                "level": level,
                "capability": capability,
                "cases": 0,
                "attacks": 0,
                "controls": 0,
                "modes": {},
            },
        )
        group["cases"] += 1
        group["controls" if pair.scenario.benign else "attacks"] += 1
        checks = {mode: item["checks"] for mode, item in inspection["modes"].items()}
        cases.append({"key": pair.key, "capability": capability, "level": level, "checks": checks})
        for mode, observations in checks.items():
            totals = group["modes"].setdefault(
                mode, {"failed_cases": 0, "failed_checks": 0, "checks": {}}
            )
            failures = sum(check["status"] == "fail" for check in observations)
            totals["failed_cases"] += int(failures > 0)
            totals["failed_checks"] += failures
            for check in observations:
                counts = totals["checks"].setdefault(
                    check["name"], {"pass": 0, "fail": 0, "unknown": 0, "not_applicable": 0}
                )
                counts[check["status"]] += 1
    statuses = Counter(
        check["status"]
        for case in cases
        for observations in case["checks"].values()
        for check in observations
    )
    return {
        "groups": list(groups.values()),
        "cases": cases,
        "case_count": len(cases),
        "status_counts": dict(statuses),
        "interpretation": "Counts describe recomputed checks on recorded cases. Failed cases and failed checks are different quantities. Unknown and not-applicable results are not passes. Security verdicts and gates are unchanged.",
    }
