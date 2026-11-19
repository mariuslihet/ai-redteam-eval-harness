"""Independent data-task answers, exact coverage and opt-in local-model evidence."""

import copy
import json
import os
import re
import time
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import JobManager
from redteam_eval.model_checks import (
    DATA_SKILLS,
    SkillQuestion,
    grade_skill,
    review_skill_results,
    skill_questions,
)
from redteam_eval.models import BackendConfig, Response
from redteam_eval.skill_coverage import skill_coverage


def data(pattern, prompt):
    return json.loads(re.search(pattern, prompt)[1])


@pytest.mark.parametrize("level", ["standard", "challenging"])
@pytest.mark.parametrize("seed", [0, 1, 42, 1729, 20261119])
def test_data_answers_against_independent_calculations(seed, level):
    questions = skill_questions(seed, 3, "data", difficulty=level)
    assert len(questions) == len({q.id for q in questions}) == 12
    assert set(q.skill for q in questions) == set(DATA_SKILLS)
    assert questions == skill_questions(seed, 3, "data", difficulty=level)
    assert all(q in questions for q in skill_questions(seed, 1, "data", difficulty=level))
    for q in questions:
        if q.skill == "Working with dates":
            schedule = data(r"Schedule: (\{.*?\})\.", q.prompt)
            start = date.fromisoformat(schedule["start_date"])
            candidates = [start + timedelta(days=i) for i in range(1, 150)]
            eligible = [
                day
                for day in candidates
                if day.isoweekday() <= 5 and day.isoformat() not in schedule["holidays"]
            ]
            due = eligible[schedule["working_days"] - 1]
            assert q.expected == {
                "due_date": due.isoformat(),
                "weekday": [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ][due.isoweekday() - 1],
            }
        elif q.skill == "Converting measurements":
            rows = data(r"Measurements: (\[.*?\])\.", q.prompt)
            exact = sum(
                Fraction(row["amount"]) * {"mm": 1, "cm": 10, "m": 1000}[row["unit"]]
                for row in rows
                if row["include"]
            )
            assert exact.denominator == 1 and exact > 0
            expected = {"total_mm": int(exact)}
            if level == "challenging":
                expected.update(full_meters=int(exact) // 1000, remainder_mm=int(exact) % 1000)
                assert any(Fraction(row["amount"]) < 0 for row in rows)
                assert any(not row["include"] for row in rows)
            assert q.expected == expected
        elif q.skill == "Applying decision rules":
            rows = data(r"Records: (\[.*?\])\.", q.prompt)
            denied = []
            for row in rows:
                ordinary_failure = not row["owner_approved"] or row["age_days"] < 7
                invalid = (
                    not row["active"]
                    or row["blocked"]
                    or (ordinary_failure and not (level == "challenging" and row["admin_override"]))
                )
                if invalid:
                    denied.append(row["id"])
            assert q.expected == {
                "allowed": sorted(row["id"] for row in rows if row["id"] not in denied),
                "denied": sorted(denied),
            }
            assert "CASE-1" in denied and "CASE-0" not in denied
        else:
            initial = data(r"Initial counters: (\{.*?\})\.", q.prompt)
            rows = data(r"Event rows: (\[.*?\])\.", q.prompt)
            selected = {}
            for row in sorted(rows, key=lambda row: row["revision"], reverse=True):
                selected.setdefault(row["id"], row)
            active = [row for row in selected.values() if not row["voided"]]
            totals = dict(initial)
            for row in active:
                totals[row["counter"]] += row["change"]
            assert q.expected == {"totals": totals, "active_events": len(active)}
            assert len(rows) > len(selected)
            if level == "challenging":
                assert len(active) < len(selected)
        # Exact-answer checker probes only; never supplied to a completion backend.
        assert grade_skill(q, Response(text=json.dumps(q.expected), finish_reason="stop"))["passed"]
        assert not grade_skill(q, Response(text="{}", finish_reason="stop"))["passed"]


def test_new_set_focus_and_ids_do_not_change_existing_sets():
    assert skill_questions(42, 2) == skill_questions(42, 2, "basic")
    full = skill_questions(42, 10, "data", difficulty="challenging")
    assert len(full) == 40
    focused = skill_questions(42, 10, "data", ["Working with dates"], "challenging")
    assert focused == [q for q in full if q.skill == "Working with dates"]
    assert not {q.id for q in full} & {
        q.id for q in skill_questions(42, 10, "extended", difficulty="challenging")
    }


def test_date_samples_cross_calendar_boundaries_and_include_weekend_holidays():
    schedules = [
        data(
            r"Schedule: (\{.*?\})\.",
            skill_questions(seed, 1, "data", ["Working with dates"], "challenging")[0].prompt,
        )
        for seed in range(100)
    ]
    assert any(
        date.fromisoformat(day).weekday() >= 5
        for schedule in schedules
        for day in schedule["holidays"]
    )
    assert any(
        date.fromisoformat(schedule["start_date"]).month
        != date.fromisoformat(
            skill_questions(seed, 1, "data", ["Working with dates"], "challenging")[0].expected[
                "due_date"
            ]
        ).month
        for seed, schedule in enumerate(schedules)
    )


@pytest.fixture
def actual_record():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text(
            encoding="utf-8"
        )
    )


def test_coverage_separates_unanswered_uncertain_failed_and_passed(actual_record):
    planned = skill_questions(
        actual_record["seed"], 2, "extended", ["Arithmetic", "Planning dependent tasks"]
    )
    partial = copy.deepcopy(actual_record)
    partial["results"] = partial["results"][:2]
    partial["results"][1]["response"]["finish_reason"] = "length"
    reviewed = review_skill_results(partial)["results"]
    coverage = skill_coverage(planned, reviewed)
    assert coverage["overall"] == dict(
        planned=4, saved=2, passed=1, failed=0, unconfirmed=1, unanswered=2
    )
    assert sum(row["planned"] for row in coverage["skills"]) == 4
    assert coverage["skills"][1]["unanswered"] == 2
    assert not coverage["inference_requested"] and not coverage["source_modified"]


def test_coverage_excludes_changed_questions_and_rejects_duplicates(actual_record):
    planned = skill_questions(
        actual_record["seed"], 2, "extended", ["Arithmetic", "Planning dependent tasks"]
    )
    reviewed = review_skill_results(actual_record)["results"]
    reviewed[0]["question"]["prompt"] += " changed"
    coverage = skill_coverage(planned, reviewed)
    assert coverage["overall"]["saved"] == 3 and coverage["overall"]["unanswered"] == 1
    assert coverage["excluded_question_ids"] == [planned[0].id]
    with pytest.raises(ValueError, match="Duplicate"):
        skill_coverage(planned, reviewed + [reviewed[1]])


def test_coverage_excludes_changed_expected_value_types(actual_record):
    planned = skill_questions(
        actual_record["seed"], 2, "extended", ["Arithmetic", "Planning dependent tasks"]
    )
    reviewed = review_skill_results(actual_record)["results"]
    reviewed[0]["question"]["expected"] = float(reviewed[0]["question"]["expected"])
    coverage = skill_coverage(planned, reviewed)
    assert coverage["overall"]["saved"] == 3
    assert coverage["excluded_question_ids"] == [planned[0].id]


@pytest.mark.local_llm
def test_actual_data_set_retains_native_replies_coverage_and_checkpoints(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_DATA_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    job = manager.submit(
        {
            "action": "check_model",
            "model": model,
            "test_suite": "data",
            "difficulty": "challenging",
            "seed": 20261119,
        }
    )
    deadline = time.monotonic() + 300
    while manager.active is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    saved = manager.snapshot(job.id)
    assert saved["status"] == "complete", saved.get("error")
    assert saved["total_checks"] == saved["completed_checks"] == 4
    assert len(manager.review_checks(job.id)["summary"]) == 4
    assert manager.performance(job.id)["coverage"]["overall"]["saved"] == 4
    assert manager.performance(job.id)["coverage"]["overall"]["unanswered"] == 0
    for item in saved["result"]["results"]:
        response = Response.model_validate(item["response"])
        assert response.text == response.response_envelope["message"]["content"]
        assert response.response_envelope["model"] == model
        assert response.model_digest == saved["result"]["model_digest"]
        assert response.usage.input_tokens > 0
        assert (
            grade_skill(SkillQuestion.model_validate(item["question"]), response) == item["grade"]
        )
    assert manager._skill_checkpoint(manager.jobs[job.id]) == saved["result"]
