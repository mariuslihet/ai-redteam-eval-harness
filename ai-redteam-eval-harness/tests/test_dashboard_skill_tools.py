"""Plans, measured reports and explanations; recorded replies stay unchanged."""

import copy
import hashlib
import json
import math
import os
import statistics
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

from redteam_eval.answer_diff import answer_differences
from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import DashboardJob, JobManager, ModelCheckRequest, now
from redteam_eval.model_checks import SkillQuestion, grade_skill, skill_questions
from redteam_eval.models import BackendConfig, Response, derive_seed
from redteam_eval.skill_tools import build_skill_plan, skill_html_report, skill_performance


@pytest.fixture
def record():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.parametrize("level", ["standard", "challenging"])
def test_expanded_plans_preserve_existing_questions_and_include_no_replies(level):
    settings = ModelCheckRequest(
        action="check_model",
        model="installed",
        test_suite="extended",
        difficulty=level,
        cases_per_skill=10,
    ).model_dump(mode="json")
    plan = build_skill_plan(settings)
    assert plan["question_count"] == len(plan["questions"]) == 80
    assert not plan["inference_requested"] and not plan["source_modified"]
    old = skill_questions(1729, 3, "extended", difficulty=level)
    for q in old:
        assert q.model_dump(mode="json") in [row["question"] for row in plan["questions"]]
    for row in plan["questions"]:
        assert row["request_seed"] == derive_seed(1729, row["question"]["id"])
        assert "response" not in row and "grade" not in row
    assert plan["maximum_output_tokens"] == 80 * 512
    assert "rough" in plan["interpretation"]


def test_small_allowance_is_warning_not_an_estimated_guarantee():
    settings = ModelCheckRequest(
        action="check_model", model="installed", max_total_tokens=1
    ).model_dump(mode="json")
    assert build_skill_plan(settings)["allowance_may_stop_run"]
    with pytest.raises(ValueError):
        ModelCheckRequest(action="check_model", model="installed", cases_per_skill=11)


def test_structured_differences_explain_keys_types_values_arrays_and_pointer_escaping():
    expected = {"x/y": {"~count": 3}, "missing": True, "items": [1, 2], "flag": False}
    actual = {"x/y": {"~count": 4}, "extra": "value", "items": [2], "flag": 0}
    differences = answer_differences(expected, actual)
    observed = {(row["path"], row["kind"]) for row in differences}
    assert {
        ("/x~1y/~0count", "value"),
        ("/missing", "missing_key"),
        ("/extra", "extra_key"),
        ("/items", "array_length"),
        ("/items/0", "value"),
        ("/flag", "type"),
    } <= observed
    assert differences == answer_differences(expected, actual)
    assert answer_differences(expected, copy.deepcopy(expected)) == []
    assert len(answer_differences(list(range(100)), [999] * 100, limit=5)) == 5


@pytest.mark.parametrize("limit", [0, 101, True, -1])
def test_invalid_difference_limits_are_rejected(limit):
    with pytest.raises(ValueError):
        answer_differences({}, {}, limit)


def test_native_fenced_wrong_json_has_precise_differences_without_changing_reply(record):
    item = next(
        row for row in record["results"] if row["question"]["skill"] == "Planning dependent tasks"
    )
    before = copy.deepcopy(item)
    grade = grade_skill(
        SkillQuestion.model_validate(item["question"]), Response.model_validate(item["response"])
    )
    assert grade["status"] == "format_error" and grade["answer_correct"] is False
    assert any(row["path"].startswith("/finish_minutes/") for row in grade["differences"])
    assert item == before


def test_performance_is_computed_from_actual_replies_and_reports_coverage(record):
    before = copy.deepcopy(record)
    data = skill_performance(record)
    times = [row["response"]["latency_ms"] for row in record["results"]]
    tokens = sum(
        sum(row["response"]["usage"][key] for key in ("input_tokens", "output_tokens"))
        for row in record["results"]
    )
    assert data["overall"]["median_reply_ms"] == statistics.median(times)
    assert data["overall"]["p95_reply_ms"] == sorted(times)[math.ceil(len(times) * 0.95) - 1]
    assert data["overall"]["total_measured_tokens"] == tokens
    assert sum(row["replies"] for row in data["skills"]) == len(record["results"])
    assert record == before
    record["results"][0]["response"]["usage"]["estimated"] = True
    del record["results"][1]["response"]["usage"]
    del record["results"][1]["response"]["latency_ms"]
    partial = skill_performance(record)["overall"]
    assert partial["measured_usage_replies"] == 2
    assert partial["estimated_usage_replies"] == partial["unknown_usage_replies"] == 1
    assert partial["timed_replies"] == 3
    empty = skill_performance({"results": []})["overall"]
    assert empty["median_reply_ms"] is empty["total_measured_tokens"] is None


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def test_portable_html_contains_original_text_and_no_active_model_markup(record):
    before = copy.deepcopy(record)
    content = skill_html_report(record, "a" * 32, "complete")
    parsed = ReportParser()
    parsed.feed(content)
    for row in record["results"]:
        assert row["response"]["text"] in "".join(parsed.text)
    assert not any(tag in {"script", "iframe", "img", "link", "form"} for tag, _ in parsed.tags)
    assert not any(
        "src" in attrs or "href" in attrs or any(key.startswith("on") for key in attrs)
        for _, attrs in parsed.tags
    )
    assert record == before
    # Escaper input only; never used as a completion backend or saved model result.
    record["results"][0]["response"]["text"] = (
        '<script>alert(1)</script><img src="https://invalid.example/x">'
    )
    content = skill_html_report(record, "<unsafe>", "cancelled")
    assert "&lt;script&gt;" in content and "<script>" not in content
    assert "&lt;unsafe&gt;" in content


def test_plan_metrics_and_html_api_preserve_sources_and_do_not_launch_jobs(tmp_path, record):
    app = DashboardApp(tmp_path, BackendConfig())
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    payload = ModelCheckRequest(
        action="check_model", model="installed", cases_per_skill=10
    ).model_dump(mode="json")
    plan = app.handle("POST", "/api/skill-plan", headers, json.dumps(payload).encode())
    assert plan.status == 200 and json.loads(plan.body)["question_count"] == 50
    assert not app.jobs.jobs and app.jobs.active is None
    assert (
        app.handle(
            "POST",
            "/api/skill-plan",
            headers | {"X-Dashboard-Token": "invalid"},
            json.dumps(payload).encode(),
        ).status
        == 403
    )
    job = DashboardJob(
        id="a" * 32,
        action="check_model",
        status="complete",
        created_at=now(),
        updated_at=now(),
        request=record["settings"],
        result=record,
    )
    app.jobs.jobs[job.id] = job
    app.jobs.save(job)
    path = app.jobs.directory / job.id / "job.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    metrics = app.handle("GET", f"/api/jobs/{job.id}/performance", headers)
    assert metrics.status == 200 and json.loads(metrics.body)["overall"]["replies"] == 4
    report = app.handle("GET", f"/api/jobs/{job.id}/report.html", headers)
    assert report.status == 200 and report.content_type.startswith("text/html")
    assert "attachment" in report.headers["Content-Disposition"]
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest() and app.jobs.active is None


@pytest.mark.local_llm
def test_actual_expanded_four_question_run_matches_preview_and_native_replies(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_EXPANDED_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    settings = ModelCheckRequest(
        action="check_model", model=model, cases_per_skill=4, selected_skills=["Arithmetic"]
    ).model_dump(mode="json")
    plan = build_skill_plan(settings)
    job = manager.submit(settings)
    deadline = time.monotonic() + 300
    while manager.active is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    saved = manager.snapshot(job.id)
    assert saved["status"] == "complete", saved.get("error")
    assert saved["completed_checks"] == saved["total_checks"] == 4
    for row, planned in zip(saved["result"]["results"], plan["questions"], strict=True):
        assert row["question"] == planned["question"]
        response = Response.model_validate(row["response"])
        assert response.text == response.response_envelope["message"]["content"]
        assert response.response_envelope["model"] == model
        assert response.request_envelope["body"]["options"]["seed"] == planned["request_seed"]
        assert "format" not in response.request_envelope["body"]
    assert manager.performance(job.id)["overall"]["replies"] == 4
    assert manager._skill_checkpoint(manager.jobs[job.id]) == saved["result"]
