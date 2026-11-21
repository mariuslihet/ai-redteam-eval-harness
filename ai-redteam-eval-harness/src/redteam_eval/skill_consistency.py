"""Read-only repeat checks over exact tasks and native saved model replies."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from pydantic import Field, model_validator

from redteam_eval.model_checks import review_skill_results
from redteam_eval.models import Model
from redteam_eval.skill_audit import native_reply


class SkillConsistencyRequest(Model):
    job_ids: list[str] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def distinct_ids(self) -> SkillConsistencyRequest:
        import re

        if len(set(self.job_ids)) != len(self.job_ids) or any(
            not re.fullmatch(r"[0-9a-f]{32}", value) for value in self.job_ids
        ):
            raise ValueError("Choose two to ten distinct saved skill tests")
        return self


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def recorded_seed(response: dict[str, Any]) -> int | None:
    request = response.get("request_envelope")
    body = request.get("body") if isinstance(request, dict) else None
    if not isinstance(body, dict):
        return None
    options = body.get("options")
    value = options.get("seed") if isinstance(options, dict) else body.get("seed")
    return value if type(value) is int else None


def repeat_category(passed: int, failed: int, unknown: int) -> str:
    if passed + failed + unknown < 2:
        return "not_repeated"
    if passed and failed:
        return "mixed_results"
    if unknown:
        return "unconfirmed"
    return "all_passing" if passed else "all_failing"


def skill_consistency(records: list[dict[str, Any]]) -> dict[str, Any]:
    SkillConsistencyRequest(job_ids=[item["job_id"] for item in records])
    if sum(len(item["record"].get("results", [])) for item in records) > 1000:
        raise ValueError("Select at most 1,000 saved replies for a consistency report")
    if (
        len(json.dumps(records, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        > 16 * 1024 * 1024
    ):
        raise ValueError("Selected reports exceed the 16 MiB consistency limit")
    groups: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    exclusions = []
    sources = []
    for source in records:
        job_id, record = source["job_id"], source["record"]
        review = review_skill_results(record)
        identifiers = [row["question"]["id"] for row in review["results"]]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate question IDs make saved evidence ambiguous")
        sources.append(
            {
                "job_id": job_id,
                "report_sha256": fingerprint(record),
                "status": record.get("status", "unknown"),
                "replies": len(identifiers),
            }
        )
        for row in review["results"]:
            question, response = row["question"], row["response"]
            reason = None
            digest = record.get("model_digest")
            if not isinstance(digest, str) or not digest or response.get("model_digest") != digest:
                reason = "Model version is missing or inconsistent"
            elif not native_reply(response, question["prompt"]):
                reason = "Native task and reply evidence is missing or inconsistent"
            elif response["response_envelope"].get("model") != record.get("model"):
                reason = "Native model name does not match the saved report"
            key = fingerprint(
                {"question": question, "model": record.get("model"), "digest": digest}
            )
            evidence = fingerprint(
                {
                    "contract": key,
                    "request": response.get("request_envelope"),
                    "reply": response.get("response_envelope"),
                }
            )
            if reason is None and evidence in seen:
                reason = "Exact duplicate native evidence; counted once"
            if reason:
                exclusions.append(
                    {"job_id": job_id, "question_id": question["id"], "reason": reason}
                )
                continue
            seen.add(evidence)
            if key not in groups:
                groups[key] = {
                    "contract_sha256": key,
                    "question": copy.deepcopy(question),
                    "model": record["model"],
                    "model_digest": digest,
                    "observations": [],
                }
            groups[key]["observations"].append(
                {"job_id": job_id, "grade": row["grade"], "response": copy.deepcopy(response)}
            )
    counts = dict.fromkeys(
        ["mixed_results", "all_passing", "all_failing", "unconfirmed", "not_repeated"], 0
    )
    output = []
    for group in groups.values():
        observations = group["observations"]
        passed = sum(row["grade"]["passed"] for row in observations)
        failed = sum(
            row["grade"]["completion"] == "complete"
            and row["grade"]["status"] in {"wrong_answer", "format_error", "unexpected_tool"}
            for row in observations
        )
        unknown = len(observations) - passed - failed
        category = repeat_category(passed, failed, unknown)
        counts[category] += 1
        recorded = [recorded_seed(row["response"]) for row in observations]
        seeds = sorted({seed for seed in recorded if seed is not None})
        output.append(
            {
                **group,
                "category": category,
                "passed": passed,
                "failed": failed,
                "unconfirmed": unknown,
                "reply_count": len(observations),
                "recorded_seeds": seeds,
                "missing_seed_replies": recorded.count(None),
            }
        )
    priority = {name: index for index, name in enumerate(counts)}
    output.sort(key=lambda group: (priority[group["category"]], group["contract_sha256"]))
    return {
        "format_version": 1,
        "sources": sources,
        "counts": counts,
        "groups": output,
        "included_replies": len(seen),
        "excluded_replies": exclusions,
        "inference_requested": False,
        "source_modified": False,
        "interpretation": "This report groups exact question contracts within the same recorded model name and version. Only verified native task/reply evidence is included; exact copied evidence counts once. Changed prompts, expected values, rules and model versions form separate groups. A mixed result requires at least one confirmed pass and one confirmed failure. Incomplete replies and checker limits remain unconfirmed. Single observations cannot establish repeatability. Repeats with the same seed may be deterministic and are not independent trials or a general reliability estimate. Author-supplied expected answers remain unverified. No source is changed and no model is called.",
    }
