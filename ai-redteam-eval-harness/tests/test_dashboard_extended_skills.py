"""Independent answer calculations and opt-in actual model checks."""

import json
import os
import re
import time
from pathlib import Path

import pytest

from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import JobManager, ModelCheckRequest
from redteam_eval.model_checks import (
    EXTENDED_SKILLS,
    grade_skill,
    review_skill_results,
    skill_names,
    skill_questions,
    skill_summary,
)
from redteam_eval.models import BackendConfig, Response


@pytest.mark.parametrize("seed", [0, 1, 42, 1729, 20261118])
def test_extended_answers_against_independent_calculations(seed):
    questions = skill_questions(seed, 3, "extended")
    assert len(questions) == len({q.id for q in questions}) == 24
    assert all(q in questions for q in skill_questions(seed, 3))
    assert questions == skill_questions(seed, 3, "extended")
    for question in questions:
        if question.skill == "Combining order records":
            prices = json.loads(re.search(r"cents: (\{.*?\})\.", question.prompt)[1])
            rows = json.loads(re.search(r"rows: (\[.*?\])\.", question.prompt)[1])
            shipping = int(re.search(r"add (\d+) cents", question.prompt)[1])
            assert len([row for row in rows if row["product"] == "P0"]) == 2
            subtotal = sum(
                sum(prices[row["product"]] for _ in range(row["quantity"]))
                for row in rows
                if row["cancelled"] is False
            )
            assert subtotal % 10 == 0
            assert question.expected == {
                "subtotal_cents": subtotal,
                "total_cents": subtotal - subtotal // 10 + shipping,
            }
        elif question.skill == "Planning dependent tasks":
            tasks = json.loads(re.search(r"Tasks: (\[.*?\])\. Work", question.prompt)[1])
            finished = {}
            # Minute-by-minute simulation, independent of the generator's recurrence.
            for minute in range(100):
                for task in tasks:
                    if task["id"] in finished:
                        continue
                    start = minute - task["minutes"]
                    if start >= 0 and all(
                        parent in finished and finished[parent] <= start for parent in task["after"]
                    ):
                        finished[task["id"]] = minute
                if len(finished) == len(tasks):
                    break
            assert len(finished) == 6
            assert question.expected == {"finish_minutes": finished, "project_minutes": minute}
        elif question.skill == "Counting exact words":
            text = re.search(r"<text>(.*?)</text>", question.prompt)[1]
            assert question.expected == {
                word: len(re.findall(r"(?:^| )" + word + r"[,.]*(?= |$)", text))
                for word in ("red", "blue")
            }
        else:
            continue
        # Checker inputs only: never persisted or displayed as model replies.
        assert grade_skill(
            question, Response(text=json.dumps(question.expected), finish_reason="stop")
        )["passed"]
        changed = dict(question.expected)
        changed[next(key for key, value in changed.items() if type(value) is int)] += 1
        assert not grade_skill(question, Response(text=json.dumps(changed), finish_reason="stop"))[
            "passed"
        ]


def test_test_set_validation_and_summary_coverage():
    assert len(skill_names()) == 5 and len(skill_names("extended")) == 8
    assert len(skill_summary([], "extended")) == 8
    for value in ("advanced", "", "mock"):
        with pytest.raises(ValueError):
            skill_questions(0, 1, value)
        with pytest.raises(ValueError):
            ModelCheckRequest(action="check_model", model="installed", test_suite=value)


@pytest.mark.parametrize("seed", [0, 42, 1729])
def test_focused_questions_preserve_ids_answers_and_seeded_data(seed):
    selected = ["Counting exact words", "Arithmetic"]
    full = skill_questions(seed, 3, "extended")
    focused = skill_questions(seed, 3, "extended", selected)
    assert focused == [question for question in full if question.skill in selected]
    assert len(focused) == 6
    assert [item["skill"] for item in skill_summary([], "extended", selected)] == [
        "Arithmetic",
        "Counting exact words",
    ]
    assert skill_questions(seed, 1, "basic", ["Arithmetic"])[0] == full[0]


@pytest.mark.parametrize(
    "selected",
    [[], ["Arithmetic", "Arithmetic"], ["Unknown"], ["Counting exact words"], [True], "Arithmetic"],
)
def test_invalid_focus_is_rejected_before_starting_inference(selected):
    with pytest.raises(ValueError):
        ModelCheckRequest.model_validate(
            {"action": "check_model", "model": "installed", "selected_skills": selected}
        )
    with pytest.raises(ValueError):
        skill_questions(42, 1, "basic", selected)


@pytest.mark.parametrize("settings", [None, [], ["Arithmetic"], "Arithmetic", 17])
def test_review_rejects_corrupt_saved_settings_with_clear_error(settings):
    with pytest.raises(ValueError, match="settings must be a JSON object"):
        review_skill_results({"results": [{}], "settings": settings})


@pytest.mark.local_llm
def test_actual_focused_run_uses_only_selected_questions_and_preserves_review(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_FOCUSED_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    selected = ["Arithmetic", "Planning dependent tasks"]
    job = manager.submit(
        {
            "action": "check_model",
            "model": model,
            "test_suite": "extended",
            "selected_skills": selected,
            "cases_per_skill": 2,
            "seed": 20261118,
        }
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(job.id)
        if snapshot["status"] in ("complete", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert snapshot["status"] == "complete", snapshot.get("error")
    assert snapshot["completed_checks"] == snapshot["total_checks"] == 4
    record = snapshot["result"]
    expected = skill_questions(20261118, 2, "extended", selected)
    assert [row["question"] for row in record["results"]] == [
        q.model_dump(mode="json") for q in expected
    ]
    assert [row["skill"] for row in record["summary"]] == selected
    assert all(row["completed"] == 2 for row in record["summary"])
    for row in record["results"]:
        response = Response.model_validate(row["response"])
        assert response.response_envelope["message"]["content"] == response.text
        assert response.response_envelope["model"] == model
    path = root / ".dashboard-jobs" / job.id / "model-checks.json"
    before = path.read_bytes()
    reviewed = manager.review_checks(job.id)
    assert [row["skill"] for row in reviewed["summary"]] == selected
    assert path.read_bytes() == before
    reloaded = JobManager(ReportStore(root), BackendConfig(model=model))
    assert reloaded.snapshot(job.id)["total_checks"] == 4
    assert reloaded.snapshot(job.id)["result"] == record


@pytest.mark.local_llm
def test_extended_run_saves_only_actual_replies_and_survives_reload(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_EXTENDED_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    job = manager.submit(
        {"action": "check_model", "model": model, "test_suite": "extended", "seed": 20261118}
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        saved = manager.snapshot(job.id)
        if saved["status"] in ("complete", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert saved["status"] == "complete", saved.get("error")
    assert saved["completed_checks"] == saved["total_checks"] == 8
    record = saved["result"]
    assert record["test_set"] == "extended-skills-v1"
    assert all(item["completed"] == 1 for item in record["summary"])
    assert set(EXTENDED_SKILLS).issubset(item["skill"] for item in record["summary"])
    for item in record["results"]:
        response = Response.model_validate(item["response"])
        assert response.response_envelope["model"] == model
        assert response.response_envelope["message"]["content"] == response.text
        assert response.usage.input_tokens > 0 and response.model_digest
        assert "format" not in response.request_envelope["body"]
    reviewed = review_skill_results(record)
    assert len(reviewed["summary"]) == 8
    assert reviewed["corrected_failures"] == 0
    restarted = JobManager(ReportStore(root), BackendConfig(model=model))
    assert restarted.snapshot(job.id)["result"] == record
    assert restarted.snapshot(job.id)["total_checks"] == 8
