"""Custom contracts, checker boundary probes and opt-in actual model inference."""

import copy
import json
import os
import time
from pathlib import Path

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import DashboardJob, JobManager, ModelCheckRequest, now
from redteam_eval.model_checks import grade_skill, review_skill_results
from redteam_eval.models import BackendConfig, Response
from redteam_eval.skill_tools import build_skill_plan, skill_html_report


def definition(**changes):
    return {
        "name": "Box count",
        "prompt": "There are 6 boxes of 7 items. How many items?",
        "answer_type": "integer",
        "expected": "42",
        **changes,
    }


def request(cases=None, **changes):
    return ModelCheckRequest(
        action="check_model",
        model="installed",
        test_suite="custom",
        custom_cases=cases if cases is not None else [definition()],
        **changes,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"name": " "},
        {"prompt": "\n"},
        {"expected": " "},
        {"name": "x" * 81},
        {"prompt": "x" * 8001},
        {"expected": "x" * 4001},
        {"answer_type": "regex"},
        {"expected": "true"},
        {"expected": "42.5"},
        {"expected": "NaN"},
        {"expected": "1e999"},
        {"expected": "9" * 400},
        {"prompt": "\ud800"},
        {"answer_type": "json", "expected": '{"a":1,"a":2}'},
        {"answer_type": "json", "expected": '{"a":[1,0.1]}'},
        {"answer_type": "json", "expected": "[1e-999]"},
        {"answer_type": "json", "expected": "[" * 257 + "0" + "]" * 257},
        {"expected": 42},
        {"surprise": "field"},
    ],
)
def test_invalid_authored_answers_fail_before_inference(changes):
    with pytest.raises(ValueError):
        request([definition(**changes)])


def test_bounds_distinct_names_and_incompatible_settings():
    for cases in [
        [],
        [definition()] * 21,
        [definition(), definition(name=" box COUNT ")],
        [definition(name=str(i), prompt="a" * 8000) for i in range(7)],
    ]:
        with pytest.raises(ValueError):
            request(cases)
    for changes in [
        {"difficulty": "challenging"},
        {"cases_per_skill": 2},
        {"selected_skills": ["Arithmetic"]},
        {"question_ids": ["unknown"]},
    ]:
        with pytest.raises(ValueError):
            request(**changes)
    with pytest.raises(ValueError):
        ModelCheckRequest(action="check_model", model="installed", custom_cases=[definition()])


def test_identity_counts_subset_and_export_round_trip():
    cases = [definition(name=str(i), prompt=f"Return {i}", expected=str(i)) for i in range(20)]
    settings = request(cases)
    questions = settings.questions()
    assert settings.question_count() == len(questions) == 20
    assert len({q.id for q in questions}) == 20
    assert request(list(reversed(cases))).questions() == list(reversed(questions))
    restored = ModelCheckRequest.model_validate_json(settings.model_dump_json())
    assert restored.questions() == questions
    subset = request(cases, question_ids=[questions[7].id, questions[2].id])
    assert subset.question_count() == 2
    assert subset.questions() == [questions[2], questions[7]]
    plan = build_skill_plan(subset.model_dump(mode="json"))
    assert plan["question_count"] == 2 and not plan["inference_requested"]
    assert "author supplied" in plan["interpretation"]
    assert not any("response" in row for row in plan["questions"])
    original = request().questions()[0]
    for key, value in [("name", "Renamed"), ("prompt", "Changed task"), ("expected", "43")]:
        assert request([definition(**{key: value})]).questions()[0].id != original.id


@pytest.mark.parametrize(
    "kind,expected,probe,status,correct",
    [
        ("json", '{"flag":true}', '{"flag":1}', "wrong_answer", False),
        ("json", '{"items":[3,1]}', '{"items":[3,1]}', "passed", True),
        ("json", "[3,1]", "[2,1]", "wrong_answer", False),
        ("json", '"0.10"', '"0.1"', "wrong_answer", False),
        ("json", '{"x":1}', '```json\n{"x":1}\n```', "format_error", True),
        ("json", '{"x":1}', '{"x":1,"x":1}', "format_error", None),
        ("integer", "42", "42.00000000000000001", "wrong_answer", False),
        ("integer", "42", " 42\n", "passed", True),
        ("text", "Code-X", "code-x", "wrong_answer", False),
        ("text", " Code-X\n", "\nCode-X ", "passed", True),
    ],
)
def test_exact_checker_contract_probes(kind, expected, probe, status, correct):
    # These strings probe the checker directly; they are never presented as model runs.
    q = request([definition(answer_type=kind, expected=expected)]).questions()[0]
    grade = grade_skill(q, Response(text=probe, finish_reason="stop"))
    assert grade["status"] == status and grade["answer_correct"] is correct
    if expected == "[3,1]":
        assert "The numbers are not in increasing order" not in grade["problems"]


def test_custom_plan_api_is_read_only_and_validates_bad_oracles(tmp_path):
    app = DashboardApp(tmp_path, BackendConfig())
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    payload = request().model_dump(mode="json")
    response = app.handle("POST", "/api/skill-plan", headers, json.dumps(payload).encode())
    assert response.status == 200
    assert json.loads(response.body)["question_count"] == 1
    bad = copy.deepcopy(payload)
    bad["custom_cases"][0]["expected"] = "not a number"
    assert app.handle("POST", "/api/skill-plan", headers, json.dumps(bad).encode()).status == 400
    assert not app.jobs.jobs and app.jobs.active is None


@pytest.mark.local_llm
def test_actual_custom_run_native_requests_reports_coverage_and_recovery(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_CUSTOM_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    cases = [
        definition(),
        definition(
            name="Selected record",
            prompt="Return the code for active=true. Records: "
            '[{"code":"ID-ALPHA","active":false},{"code":"ID-BETA","active":true}]',
            answer_type="text",
            expected="ID-BETA",
        ),
        definition(
            name="Nested result",
            prompt='Return JSON with key "counts" containing '
            'an object with "remaining" equal to 20 minus 3 and "ready" equal to true.',
            answer_type="json",
            expected='{"counts":{"remaining":17,"ready":true}}',
        ),
    ]
    settings = request(cases).model_dump(mode="json") | {"model": model}
    plan = build_skill_plan(settings)
    job = manager.submit(settings)
    deadline = time.monotonic() + 300
    while manager.active is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    saved = manager.snapshot(job.id)
    assert saved["status"] == "complete", saved.get("error")
    assert saved["completed_checks"] == saved["total_checks"] == 3
    source = copy.deepcopy(saved["result"])
    for row, planned in zip(source["results"], plan["questions"], strict=True):
        assert row["question"] == planned["question"]
        response = Response.model_validate(row["response"])
        assert response.text == response.response_envelope["message"]["content"]
        assert response.response_envelope["model"] == model
        body = response.request_envelope["body"]
        assert "format" not in body
        assert body["messages"][-1]["content"] == planned["question"]["prompt"]
        assert "expected" not in body and "custom_cases" not in body
    assert manager._skill_checkpoint(manager.jobs[job.id]) == source
    assert manager.performance(job.id)["coverage"]["overall"]["saved"] == 3
    assert len(review_skill_results(source)["summary"]) == 1
    assert "author-supplied" in skill_html_report(source, job.id, "complete")
    assert manager.snapshot(job.id)["result"] == source
    # A modified authored contract must never resume with an old native reply.
    manager.jobs[job.id].request["custom_cases"][0]["expected"] = "43"
    with pytest.raises(ValueError):
        manager._skill_checkpoint(manager.jobs[job.id])


def test_twenty_custom_questions_have_accurate_progress_before_any_reply(tmp_path):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    settings = request([definition(name=str(i)) for i in range(20)])
    job = DashboardJob(
        id="a" * 32,
        action="check_model",
        status="cancelled",
        created_at=now(),
        updated_at=now(),
        request=settings.model_dump(mode="json"),
    )
    manager.jobs[job.id] = job
    snapshot = manager.snapshot(job.id)
    assert snapshot["total_checks"] == snapshot["remaining_checks"] == 20
    assert snapshot["completed_checks"] == 0 and not snapshot["can_continue"]


def test_authored_json_depth_boundary_is_validated_before_running():
    request([definition(answer_type="json", expected="[" * 32 + "0" + "]" * 32)])
    with pytest.raises(ValueError, match="32 nested"):
        request([definition(answer_type="json", expected="[" * 33 + "0" + "]" * 33)])
