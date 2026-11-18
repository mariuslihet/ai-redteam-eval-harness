"""Question generation, answer checks and actual installed-model skill tests."""

import json
import os
import re
import time
from pathlib import Path

import pytest

from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import JobManager, ModelCheckRequest
from redteam_eval.model_checks import (
    SKILLS,
    SkillQuestion,
    exact_value,
    grade_skill,
    skill_questions,
)
from redteam_eval.models import BackendConfig, Response


@pytest.mark.parametrize("seed", [0, 1, 42, 1729, 20261117])
def test_questions_are_seeded_and_expansion_preserves_existing_questions(seed):
    small = skill_questions(seed, 1)
    large = skill_questions(seed, 3)
    assert small == skill_questions(seed, 1)
    assert len(large) == 15 and len({q.id for q in large}) == 15
    assert {q.skill for q in large} == set(SKILLS)
    assert all(q in large for q in small)
    assert small != skill_questions(seed + 1, 1)
    for q in large:
        if q.skill == "Arithmetic":
            boxes, items, removed = map(
                int,
                re.search(
                    r"There are (\d+) boxes with (\d+) items each\. (\d+) items", q.prompt
                ).groups(),
            )
            assert q.expected == boxes * items - removed
        elif q.skill == "Finding the right record":
            route = json.loads(re.search(r"Use the route (\{.*?\})", q.prompt).group(1))
            rows = json.loads(re.search(r"Records: (\[.*\])\. Reply", q.prompt).group(1))
            matching = [
                row
                for row in rows
                if row["id"] == route["id"] and row["revision"] == route["revision"]
            ]
            assert len(matching) == 1 and matching[0]["value"] == q.expected
        elif q.skill == "Sorting and removing duplicates":
            values = json.loads(re.search(r"duplicates: (\[.*\])\.", q.prompt).group(1))
            assert q.expected == sorted(set(values))


@pytest.mark.parametrize("count", [0, 4, -1])
def test_invalid_question_counts_are_rejected(count):
    with pytest.raises(ValueError):
        skill_questions(0, count)


@pytest.mark.parametrize("seed", [0, 42, 1729])
def test_computed_answers_satisfy_the_answer_checker(seed):
    # Parser validation inputs only, never a completion backend or displayed model reply.
    for question in skill_questions(seed, 2):
        text = (
            json.dumps(question.expected)
            if question.answer_type == "json"
            else str(question.expected)
        )
        result = grade_skill(question, Response(text=text, finish_reason="stop"))
        assert result["passed"] and not result["problems"]


@pytest.mark.parametrize(
    "text",
    [
        '{"x":1,"x":1}',
        '{"x":true}',
        '{"x":"1"}',
        '{"x":1,"extra":0}',
        '```json\n{"x":1}\n```',
        '{"x":NaN}',
        '{"x":1} extra',
    ],
)
def test_strict_json_rejects_duplicate_keys_wrong_types_and_extra_text(text):
    question = SkillQuestion(
        id="validator-input",
        skill="JSON output",
        prompt="validator input",
        expected={"x": 1},
        answer_type="json",
        rule="Exact object",
    )
    assert not grade_skill(question, Response(text=text, finish_reason="stop"))["passed"]


def test_checker_rejects_cut_off_or_unfinished_answers_even_when_text_matches():
    question = skill_questions(42, 1)[0]
    assert not grade_skill(question, Response(text=str(question.expected), finish_reason="length"))[
        "passed"
    ]
    assert not grade_skill(question, Response(text=str(question.expected)))["passed"]
    assert not exact_value(True, 1) and not exact_value([1.0], [1])
    assert exact_value({"a": 1, "b": False}, {"b": False, "a": 1})


def test_sorting_failure_explains_missing_added_and_out_of_order_numbers():
    question = SkillQuestion(
        id="validator-input",
        skill=SKILLS[-1],
        prompt="validator input",
        expected=[-25, -1, 4],
        answer_type="json",
        rule="Sort unique integers",
    )
    result = grade_skill(question, Response(text="[4,-5,-1,-1]", finish_reason="stop"))
    assert not result["passed"]
    assert "Numbers missing: -25" in result["problems"]
    assert "Numbers not in the input: -5" in result["problems"]
    assert "The numbers are not in increasing order" in result["problems"]


@pytest.mark.parametrize(
    "extra",
    [
        {"cases_per_skill": 4},
        {"seed": -1},
        {"max_run_seconds": float("inf")},
        {"max_total_tokens": 0},
        {"fake_responses": []},
    ],
)
def test_skill_request_rejects_invalid_limits_and_response_injection(extra):
    with pytest.raises(ValueError):
        ModelCheckRequest.model_validate(
            {"action": "check_model", "model": "installed-model"} | extra
        )


def wait(manager, identifier, predicate, seconds=360):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(identifier)
        if predicate(snapshot):
            return snapshot
        time.sleep(0.1)
    pytest.fail(f"Skill worker did not finish: {snapshot}")


@pytest.mark.local_llm
def test_actual_model_skills_cancellation_limits_and_saved_native_replies(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_SKILLS_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    first = manager.submit({"action": "check_model", "model": model, "cases_per_skill": 3})
    snapshot = wait(
        manager, first.id, lambda s: s["completed_checks"] > 0 or s["status"] == "failed"
    )
    assert snapshot["status"] != "failed", snapshot["error"]
    manager.cancel(first.id)
    stopped = wait(manager, first.id, lambda s: s["status"] == "cancelled")
    assert 0 < len(stopped["result"]["results"]) < 15
    assert stopped["result"]["status"] == "cancelled"
    complete = manager.submit(
        {"action": "check_model", "model": model, "seed": 20261117, "cases_per_skill": 2}
    )
    done = wait(manager, complete.id, lambda s: s["status"] in ("complete", "failed"))
    assert done["status"] == "complete", done["error"]
    assert done["completed_checks"] == done["total_checks"] == 10
    assert all(item["completed"] == 2 for item in done["result"]["summary"])
    for item in done["result"]["results"]:
        response = Response.model_validate(item["response"])
        assert response.response_envelope["model"] == model
        assert response.model_digest == done["result"]["model_digest"]
        assert response.usage.input_tokens > 0
        assert "format" not in response.request_envelope["body"]
        assert (
            grade_skill(SkillQuestion.model_validate(item["question"]), response) == item["grade"]
        )
    limited = manager.submit({"action": "check_model", "model": model, "max_total_tokens": 1})
    failed = wait(manager, limited.id, lambda s: s["status"] == "failed")
    assert failed["result"]["status"] == "budget_exceeded"
    assert failed["completed_checks"] == 1
    assert len(failed["result"]["results"]) == 1


@pytest.mark.local_llm
async def test_actual_numeric_replies_remain_safe_to_grade_and_save(tmp_path):
    from redteam_eval.backends import HTTPBackend
    from redteam_eval.models import Message, Request

    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    manager = JobManager(ReportStore(tmp_path), BackendConfig(model=model))
    backend = HTTPBackend(await manager.selected_backend(model))
    results = []
    for literal in ('{"x":1.0}', '{"x":1e9999}'):
        q = SkillQuestion(
            id="numeric-probe",
            skill="JSON output",
            prompt="Reply with this JSON object only, without Markdown: " + literal,
            expected={"x": 1},
            answer_type="json",
            rule="Actual model numeric-format probe",
        )
        response = await backend.generate(
            Request(
                messages=[Message(role="user", content=q.prompt)], seed=20261118, max_tokens=128
            )
        )
        grade = grade_skill(q, response)
        assert response.response_envelope["model"] == model
        results.append({"response": response.model_dump(mode="json"), "grade": grade})
        json.dumps(results, allow_nan=False)
        if response.text.strip() == '{"x":1.0}':
            assert grade["passed"]
        if "1e9999" in response.text:
            assert not grade["passed"]
