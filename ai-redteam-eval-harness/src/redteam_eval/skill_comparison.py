"""Read-only comparisons of identical questions and their actual saved replies."""

from __future__ import annotations

import math
from typing import Any

from redteam_eval.model_checks import exact_value, review_skill_results


def outcome_change(previous: dict[str, Any], current: dict[str, Any]) -> str:
    confirmed = {"passed", "wrong_answer", "format_error", "unexpected_tool"}
    if any(
        grade["status"] not in confirmed or grade.get("completion") != "complete"
        for grade in (previous, current)
    ):
        return "unconfirmed"
    if previous["passed"] and current["passed"]:
        return "still_passing"
    if not previous["passed"] and not current["passed"]:
        return "still_failing"
    return "regressed" if previous["passed"] else "improved"


def recorded_measurements(response: dict[str, Any]) -> dict[str, int | float | None]:
    usage = response.get("usage")
    tokens = None
    if isinstance(usage, dict) and usage.get("estimated") is False:
        counts: list[Any] = [usage.get(key) for key in ("input_tokens", "output_tokens")]
        if all(type(count) is int and count >= 0 for count in counts):
            tokens = sum(counts)
    latency = response.get("latency_ms")
    try:
        valid_latency = (
            isinstance(latency, (int, float))
            and not isinstance(latency, bool)
            and math.isfinite(latency)
            and latency >= 0
        )
    except OverflowError:
        valid_latency = False
    if not valid_latency:
        latency = None
    return {"tokens": tokens, "latency_ms": latency}


def measurement_delta(
    previous: int | float | None, current: int | float | None
) -> int | float | None:
    return current - previous if previous is not None and current is not None else None


def compare_skill_results(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    reviews = [review_skill_results(record) for record in (previous, current)]
    indexed = []
    for review in reviews:
        rows = {item["question"]["id"]: item for item in review["results"]}
        if len(rows) != len(review["results"]):
            raise ValueError("Saved test contains duplicate question IDs; comparison is ambiguous")
        indexed.append(rows)
    old, new = indexed
    matched, changed = [], []
    for identifier, before in old.items():
        if identifier not in new:
            continue
        after = new[identifier]
        if not exact_value(before["question"], after["question"]):
            changed.append(identifier)
            continue
        before_metrics, after_metrics = (
            recorded_measurements(item["response"]) for item in (before, after)
        )
        matched.append(
            {
                "question": before["question"],
                "previous": before,
                "current": after,
                "change": outcome_change(before["grade"], after["grade"]),
                "measurements": {
                    name: {
                        "previous": before_metrics[name],
                        "current": after_metrics[name],
                        "delta": measurement_delta(before_metrics[name], after_metrics[name]),
                    }
                    for name in ("tokens", "latency_ms")
                },
            }
        )
    measurements = {}
    for name in ("tokens", "latency_ms"):
        known = [
            row["measurements"][name]
            for row in matched
            if row["measurements"][name]["delta"] is not None
        ]
        measurements[name] = {
            "matched_pairs_with_measurements": len(known),
            "previous_total": sum(row["previous"] for row in known) if known else None,
            "current_total": sum(row["current"] for row in known) if known else None,
            "delta": sum(row["delta"] for row in known) if known else None,
        }
    return {
        "format_version": 1,
        "reviewer_version": reviews[0]["reviewer_version"],
        "previous": {
            key: previous.get(key)
            for key in ("model", "model_digest", "seed", "status", "test_set", "settings")
        },
        "current": {
            key: current.get(key)
            for key in ("model", "model_digest", "seed", "status", "test_set", "settings")
        },
        "previous_reply_count": len(old),
        "current_reply_count": len(new),
        "matched_count": len(matched),
        "changed_question_ids": changed,
        "previous_only_ids": [identifier for identifier in old if identifier not in new],
        "current_only_ids": [identifier for identifier in new if identifier not in old],
        "counts": {
            name: sum(row["change"] == name for row in matched)
            for name in ("improved", "regressed", "still_passing", "still_failing", "unconfirmed")
        },
        "measurements": measurements,
        "results": matched,
        "inference_requested": False,
        "source_modified": False,
        "interpretation": "Only identical saved questions are compared. Both replies are checked with the current checker; original saved pass/fail flags are retained. Unfinished or unconfirmed replies are not counted as improvements or regressions. Measurements cover only matched pairs with recorded data on both sides; estimated token counts are excluded. Time and token differences describe these saved replies, not general model speed or quality. Changed questions and missing answers are excluded, not treated as failures.",
    }
