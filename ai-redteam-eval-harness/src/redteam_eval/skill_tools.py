"""Inference-free test plans, measured summaries and portable HTML reports."""

from __future__ import annotations

import html
import json
import math
import statistics
from typing import Any

from redteam_eval.model_checks import SKILL_SYSTEM_MESSAGE, review_skill_results, skill_questions
from redteam_eval.models import derive_seed
from redteam_eval.skill_comparison import recorded_measurements


def build_skill_plan(settings: dict[str, Any]) -> dict[str, Any]:
    questions = skill_questions(
        settings["seed"],
        settings["cases_per_skill"],
        settings["test_suite"],
        settings.get("selected_skills"),
        settings.get("difficulty", "standard"),
        settings.get("question_ids"),
        settings.get("custom_cases"),
    )
    rows: list[dict[str, Any]] = [
        {
            "question": q.model_dump(mode="json"),
            "request_seed": derive_seed(settings["seed"], q.id),
            "prompt_characters": len(q.prompt) + len(SKILL_SYSTEM_MESSAGE),
            "estimated_input_tokens": math.ceil((len(q.prompt) + len(SKILL_SYSTEM_MESSAGE)) / 4),
        }
        for q in questions
    ]
    estimate = sum(row["estimated_input_tokens"] for row in rows)
    maximum_output = len(rows) * settings["max_tokens"]
    return {
        "format_version": 1,
        "kind": "test_plan",
        "settings": settings,
        "system_message": SKILL_SYSTEM_MESSAGE,
        "question_count": len(rows),
        "estimated_input_tokens": estimate,
        "maximum_output_tokens": maximum_output,
        "estimated_input_plus_maximum_output": estimate + maximum_output,
        "allowance_may_stop_run": estimate + maximum_output > settings["max_total_tokens"],
        "questions": rows,
        "inference_requested": False,
        "source_modified": False,
        "interpretation": "This is a generated test plan, not model output. Expected answers are included for inspection and are not added to the model request. Custom expected answers are author supplied and are not independently verified. Input tokens are a rough characters/4 estimate, not tokenizer measurements; chat framing, cache behavior and model tokenization can differ. Maximum output is a limit, not predicted usage. Time and token allowances can stop a run early.",
    }


def skill_performance(record: dict[str, Any]) -> dict[str, Any]:
    results = record.get("results")
    if not isinstance(results, list):
        raise ValueError("Saved skill report has no reply list")
    if len(results) > 1000 or any(
        not isinstance(row, dict)
        or not isinstance(row.get("question"), dict)
        or not isinstance(row["question"].get("skill"), str)
        or not isinstance(row.get("response"), dict)
        for row in results
    ):
        raise ValueError("Saved skill rows need a question, skill name and reply")

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        timings, measured, estimated, unknown_usage = [], [], 0, 0
        for item in rows:
            response = item.get("response")
            if not isinstance(response, dict):
                raise ValueError("Saved reply must be an object")
            values = recorded_measurements(response)
            if values["latency_ms"] is not None:
                timings.append(values["latency_ms"])
            if values["tokens"] is not None:
                measured.append(values["tokens"])
            elif (
                isinstance(response.get("usage"), dict)
                and response["usage"].get("estimated") is True
            ):
                estimated += 1
            else:
                unknown_usage += 1
        ordered = sorted(timings)
        return {
            "replies": len(rows),
            "timed_replies": len(timings),
            "median_reply_ms": statistics.median(timings) if timings else None,
            "p95_reply_ms": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
            "total_measured_tokens": sum(measured) if measured else None,
            "measured_usage_replies": len(measured),
            "estimated_usage_replies": estimated,
            "unknown_usage_replies": unknown_usage,
        }

    groups = list(dict.fromkeys(item["question"]["skill"] for item in results))
    return {
        "overall": summarize(results),
        "skills": [
            {
                "skill": skill,
                **summarize([item for item in results if item["question"]["skill"] == skill]),
            }
            for skill in groups
        ],
        "inference_requested": False,
        "source_modified": False,
        "interpretation": "Measurements cover saved replies, including restored replies once each. Missing timings stay unknown. Median is the middle observed duration; p95 uses nearest rank and is only a sample statistic, especially for small sets. Token totals exclude estimated or missing usage. Recorded reply time includes the backend request, not just token generation.",
    }


def skill_html_report(record: dict[str, Any], job_id: str, status: str) -> str:
    review = review_skill_results(record)
    performance = skill_performance(record)

    def escape(value: Any) -> str:
        return html.escape(str(value), quote=True)

    def pretty(value: Any) -> str:
        return escape(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))

    sections = []
    for row in review["results"]:
        grade, q = row["grade"], row["question"]
        sections.append(
            f"<section><h2>{escape(q['skill'])} · {escape(grade['status'])}</h2><p>Question {escape(q['id'])} · Original saved result: {'passed' if row['previous_passed'] else 'failed'}. Current check: {escape(grade['status'])}.</p><h3>Task</h3><pre>{escape(q['prompt'])}</pre><h3>Actual model reply</h3><pre>{escape(row['response'].get('text', ''))}</pre><h3>Expected answer</h3><pre>{pretty(q['expected'])}</pre><p>{escape(q['rule'])}</p><h3>Current check details</h3><pre>{pretty(grade)}</pre></section>"
        )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>Model skill report</title><style>body{{font:15px/1.6 system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 20px;color:#172c3d}}section{{border-top:1px solid #ccd7df;margin-top:28px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f7;padding:16px;border-radius:6px}}h1,h2,h3{{line-height:1.3}}p{{overflow-wrap:anywhere}}@media print{{section{{break-inside:avoid}}}}</style></head><body><h1>Model skill report</h1><p>Saved run {escape(job_id)} · {escape(status)} · {len(review["results"])} actual saved replies</p><p>Model: {escape(record.get("model"))}<br>Model version: {escape(record.get("model_digest"))}</p><p>This portable report contains original saved replies and current answer checks. It makes no model calls. An incomplete run includes only available replies. No scripts or external resources are required.</p><h2>Saved settings</h2><pre>{pretty(record.get("settings", {}))}</pre><h2>Recorded performance</h2><pre>{pretty(performance)}</pre>{"".join(sections)}<details><summary>Original saved report</summary><pre>{pretty(record)}</pre></details></body></html>"""
