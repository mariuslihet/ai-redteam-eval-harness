"""Saved-reply comparison over recorded inference and checker-only boundary inputs."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_jobs import DashboardJob, now
from redteam_eval.models import BackendConfig
from redteam_eval.skill_comparison import (
    compare_skill_results,
    outcome_change,
    recorded_measurements,
)


@pytest.fixture
def record():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text(
            encoding="utf-8"
        )
    )


def test_same_saved_run_has_no_improvements_or_regressions_and_no_source_edits(record):
    before = copy.deepcopy(record)
    comparison = compare_skill_results(record, record)
    assert comparison["matched_count"] == 4
    assert comparison["counts"]["improved"] == comparison["counts"]["regressed"] == 0
    assert sum(comparison["counts"].values()) == 4
    assert comparison["measurements"]["tokens"]["delta"] == 0
    assert comparison["measurements"]["latency_ms"]["delta"] == 0
    assert not comparison["source_modified"] and not comparison["inference_requested"]
    assert record == before
    for pair in comparison["results"]:
        original = next(
            row for row in record["results"] if row["question"]["id"] == pair["question"]["id"]
        )
        assert pair["previous"]["response"] == pair["current"]["response"] == original["response"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt", "changed task"),
        ("expected", 999),
        ("rule", "changed rules"),
        ("answer_type", "text"),
        ("skill", "JSON output"),
    ],
)
def test_changed_question_content_is_excluded_even_with_same_id(record, field, value):
    changed = copy.deepcopy(record)
    changed["results"][0]["question"][field] = value
    comparison = compare_skill_results(record, changed)
    assert comparison["matched_count"] == 3
    assert comparison["changed_question_ids"] == [record["results"][0]["question"]["id"]]
    assert comparison["counts"]["regressed"] == 0


def test_missing_answers_are_excluded_not_failed_and_order_does_not_matter(record):
    partial = copy.deepcopy(record)
    partial["results"] = list(reversed(partial["results"][:2]))
    partial["status"] = "cancelled"
    comparison = compare_skill_results(record, partial)
    assert comparison["matched_count"] == 2
    assert len(comparison["previous_only_ids"]) == 2
    assert comparison["current_only_ids"] == []
    assert comparison["counts"]["regressed"] == 0
    assert comparison["current"]["status"] == "cancelled"


def test_no_matching_questions_has_explicit_empty_scope_and_unknown_measurements(record):
    other = copy.deepcopy(record)
    for row in other["results"]:
        row["question"]["id"] += "-other"
    comparison = compare_skill_results(record, other)
    assert comparison["matched_count"] == 0
    assert len(comparison["previous_only_ids"]) == len(comparison["current_only_ids"]) == 4
    assert comparison["measurements"]["tokens"]["delta"] is None
    assert comparison["measurements"]["latency_ms"]["matched_pairs_with_measurements"] == 0


def test_duplicate_ids_cannot_produce_ambiguous_comparison(record):
    record["results"].append(copy.deepcopy(record["results"][0]))
    with pytest.raises(ValueError, match="duplicate question IDs"):
        compare_skill_results(record, record)


@pytest.mark.parametrize(
    "earlier,later,expected",
    [
        ("passed", "wrong_answer", "regressed"),
        ("wrong_answer", "passed", "improved"),
        ("format_error", "passed", "improved"),
        ("passed", "format_error", "regressed"),
        ("wrong_answer", "format_error", "still_failing"),
        ("passed", "passed", "still_passing"),
        ("passed", "needs_review", "unconfirmed"),
        ("incomplete", "passed", "unconfirmed"),
        ("unexpected_tool", "passed", "improved"),
    ],
)
def test_outcome_transitions_distinguish_confirmed_and_uncertain_results(earlier, later, expected):
    def grade(status):
        return {
            "status": status,
            "passed": status == "passed",
            "completion": "cut_off"
            if status == "incomplete"
            else "unknown"
            if status == "needs_review"
            else "complete",
        }

    assert outcome_change(grade(earlier), grade(later)) == expected


def test_saved_pass_flags_are_rechecked_and_missing_completion_is_unconfirmed(record):
    other = copy.deepcopy(record)
    for row in other["results"]:
        row["grade"]["passed"] = not row["grade"]["passed"]
    comparison = compare_skill_results(record, other)
    assert comparison["counts"]["improved"] == comparison["counts"]["regressed"] == 0
    assert (
        comparison["results"][0]["current"]["previous_passed"]
        != comparison["results"][0]["previous"]["previous_passed"]
    )
    other["results"][0]["response"]["finish_reason"] = None
    assert compare_skill_results(record, other)["results"][0]["change"] == "still_passing"
    del other["results"][0]["response"]["response_envelope"]["done_reason"]
    comparison = compare_skill_results(record, other)
    assert comparison["results"][0]["change"] == "unconfirmed"
    assert comparison["counts"]["regressed"] == 0


def test_measurement_deltas_use_only_recorded_pairs_with_explicit_metadata(record):
    other = copy.deepcopy(record)
    first = other["results"][0]["response"]
    first["usage"]["output_tokens"] += 7
    first["latency_ms"] += 120
    other["results"][1]["response"]["usage"]["estimated"] = True
    del other["results"][1]["response"]["latency_ms"]
    comparison = compare_skill_results(record, other)
    assert comparison["measurements"]["tokens"]["delta"] == 7
    assert comparison["measurements"]["tokens"]["matched_pairs_with_measurements"] == 3
    assert comparison["measurements"]["latency_ms"]["delta"] == pytest.approx(120)
    assert comparison["results"][1]["measurements"]["tokens"]["delta"] is None
    assert comparison["results"][1]["measurements"]["latency_ms"]["delta"] is None
    assert recorded_measurements({}) == {"tokens": None, "latency_ms": None}
    assert recorded_measurements(
        {
            "usage": {"input_tokens": True, "output_tokens": 2, "estimated": False},
            "latency_ms": False,
        }
    ) == {"tokens": None, "latency_ms": None}


@pytest.mark.parametrize("latency", [float("inf"), -1, "5", True, 10**1000])
def test_unsupported_timing_values_stay_unknown_instead_of_crashing(latency):
    assert recorded_measurements({"latency_ms": latency})["latency_ms"] is None


def test_comparison_api_is_protected_read_only_and_does_not_start_inference(tmp_path, record):
    app = DashboardApp(tmp_path, BackendConfig())
    jobs = []
    for identifier in ("a" * 32, "b" * 32):
        job = DashboardJob(
            id=identifier,
            action="check_model",
            status="complete",
            created_at=now(),
            updated_at=now(),
            request=record["settings"],
            result=copy.deepcopy(record),
        )
        app.jobs.jobs[job.id] = job
        app.jobs.save(job)
        jobs.append(job)
    paths = [app.jobs.directory / job.id / "job.json" for job in jobs]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "X-Dashboard-Token": app.csrf,
        "Content-Type": "application/json",
    }
    payload = json.dumps({"previous": jobs[0].id, "current": jobs[1].id}).encode()
    reply = app.handle("POST", "/api/skill-comparison", headers, payload)
    assert reply.status == 200
    data = json.loads(reply.body)
    assert data["matched_count"] == 4 and not data["same_source"]
    assert app.jobs.active is None
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths] == hashes
    assert (
        app.handle(
            "POST", "/api/skill-comparison", headers | {"X-Dashboard-Token": "invalid"}, payload
        ).status
        == 403
    )
    assert (
        app.handle(
            "POST", "/api/skill-comparison", headers, b'{"previous":"bad","current":"bad"}'
        ).status
        == 400
    )
    assert (
        app.handle("POST", "/api/skill-comparison", headers, payload[:-1] + b',"save":true}').status
        == 400
    )
    assert (
        app.handle(
            "POST",
            "/api/skill-comparison",
            headers,
            json.dumps({"previous": "c" * 32, "current": jobs[0].id}).encode(),
        ).status
        == 404
    )
