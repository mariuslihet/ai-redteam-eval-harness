"""Native saved evidence, repeat classification boundaries and actual inference."""

import copy
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import DashboardJob, JobManager, ModelCheckRequest, now
from redteam_eval.models import BackendConfig
from redteam_eval.skill_consistency import (
    SkillConsistencyRequest,
    recorded_seed,
    repeat_category,
    skill_consistency,
)


@pytest.fixture
def record():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text()
    )


def sources(record, other=None):
    return [
        {"job_id": "a" * 32, "record": record},
        {"job_id": "b" * 32, "record": copy.deepcopy(record) if other is None else other},
    ]


def register(manager, record, identifier):
    job = DashboardJob(
        id=identifier,
        action="check_model",
        status="complete",
        created_at=now(),
        updated_at=now(),
        request=copy.deepcopy(record["settings"]),
        result=copy.deepcopy(record),
    )
    manager.jobs[job.id] = job
    manager.save(job)
    return job


@pytest.mark.parametrize(
    "passed,failed,unknown,category",
    [
        (1, 0, 0, "not_repeated"),
        (0, 1, 0, "not_repeated"),
        (0, 0, 1, "not_repeated"),
        (2, 0, 0, "all_passing"),
        (0, 3, 0, "all_failing"),
        (1, 1, 0, "mixed_results"),
        (2, 1, 1, "mixed_results"),
        (1, 0, 1, "unconfirmed"),
        (0, 2, 1, "unconfirmed"),
        (0, 0, 2, "unconfirmed"),
    ],
)
def test_classification_requires_confirmed_evidence(passed, failed, unknown, category):
    assert repeat_category(passed, failed, unknown) == category


@pytest.mark.parametrize(
    "ids",
    [
        [],
        ["a" * 32],
        ["a" * 32] * 2,
        ["../x", "b" * 32],
        ["A" * 32, "b" * 32],
        [str(i).zfill(32) for i in range(11)],
    ],
)
def test_selection_bounds_and_distinct_identity(ids):
    with pytest.raises(ValueError):
        SkillConsistencyRequest(job_ids=ids)


def test_exact_native_copies_count_once_and_do_not_prove_repeatability(record):
    before = copy.deepcopy(record)
    report = skill_consistency(sources(record))
    assert report["included_replies"] == len(record["results"]) == 4
    assert len(report["excluded_replies"]) == 4
    assert report["counts"]["not_repeated"] == 4
    assert not report["counts"]["all_passing"] and not report["counts"]["all_failing"]
    assert all("duplicate native" in item["reason"] for item in report["excluded_replies"])
    assert not report["inference_requested"] and not report["source_modified"]
    assert record == before
    # Full real envelopes remain available in the exported observations.
    assert {group["observations"][0]["response"]["text"] for group in report["groups"]} == {
        row["response"]["text"] for row in record["results"]
    }


@pytest.mark.parametrize("field", ["expected", "rule", "prompt", "model_digest"])
def test_changed_contracts_and_versions_cannot_merge(record, field):
    # Mutated metadata is a comparison-boundary probe, never a saved model run.
    changed = copy.deepcopy(record)
    if field == "model_digest":
        changed[field] = "different-version"
        for row in changed["results"]:
            row["response"][field] = changed[field]
    else:
        for row in changed["results"]:
            row["question"][field] = "Changed contract"
    report = skill_consistency(sources(record, changed))
    assert not report["counts"]["mixed_results"]
    assert all(group["reply_count"] == 1 for group in report["groups"])
    if field == "prompt":
        assert len(report["excluded_replies"]) == 4
    else:
        assert len(report["groups"]) == 8


@pytest.mark.parametrize("kind", ["wrapped", "missing_digest", "wrong_digest", "wrong_model"])
def test_unverified_or_inconsistent_native_evidence_is_excluded(record, kind):
    altered = copy.deepcopy(record)
    for row in altered["results"]:
        if kind == "wrapped":
            row["response"]["text"] += " wrapper changes"
        elif kind == "missing_digest":
            row["response"].pop("model_digest")
        elif kind == "wrong_digest":
            row["response"]["model_digest"] = "wrong"
        else:
            row["response"]["response_envelope"]["model"] = "other"
    report = skill_consistency(sources(record, altered))
    assert report["included_replies"] == 4 and len(report["excluded_replies"]) == 4
    assert report["counts"]["not_repeated"] == 4


def test_duplicate_questions_and_work_limits_reject_ambiguous_reports(record):
    bad = copy.deepcopy(record)
    bad["results"].append(copy.deepcopy(bad["results"][0]))
    with pytest.raises(ValueError, match="Duplicate question"):
        skill_consistency(sources(record, bad))
    large = copy.deepcopy(record)
    large["results"] *= 251
    with pytest.raises(ValueError, match="1,000"):
        skill_consistency(sources(record, large))


def test_read_only_api_preserves_files_rejects_active_runs_and_requires_session(tmp_path, record):
    app = DashboardApp(tmp_path, BackendConfig())
    for identifier in ["a" * 32, "b" * 32]:
        register(app.jobs, record, identifier)
    files = list(tmp_path.rglob("job.json"))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    payload = json.dumps({"job_ids": ["a" * 32, "b" * 32]}).encode()
    reply = app.handle("POST", "/api/skill-consistency", headers, payload)
    assert reply.status == 200 and json.loads(reply.body)["included_replies"] == 4
    assert app.jobs.active is None and len(app.jobs.jobs) == 2
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert (
        app.handle(
            "POST", "/api/skill-consistency", headers | {"X-Dashboard-Token": "bad"}, payload
        ).status
        == 403
    )
    app.jobs.jobs["b" * 32].status = "running"
    assert app.handle("POST", "/api/skill-consistency", headers, payload).status == 400


def test_repeat_copies_exact_selected_plan_and_limits_without_modifying_source(
    tmp_path, record, monkeypatch
):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record, "a" * 32)
    before = manager.snapshot(source.id)
    # Inspect queued work without starting an inference thread; no completion is invented.
    monkeypatch.setattr(manager, "_start", lambda job: None)
    repeated = manager.repeat_checks(source.id)
    assert repeated.id != source.id and repeated.result is None
    assert repeated.request == ModelCheckRequest.model_validate(source.request).model_dump(
        mode="json"
    ) | {"retry_of": None}
    assert (
        ModelCheckRequest.model_validate(repeated.request).questions()
        == ModelCheckRequest.model_validate(source.request).questions()
    )
    assert manager.snapshot(source.id) == before
    source.status = "running"
    with pytest.raises(ValueError):
        manager.repeat_checks(source.id)


@pytest.mark.local_llm
def test_actual_repeat_preserves_source_and_reports_native_observations(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_REPEAT_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    settings = ModelCheckRequest(
        action="check_model",
        model=model,
        test_suite="data",
        selected_skills=["Reconciling event records"],
        difficulty="challenging",
    ).model_dump(mode="json")
    source = manager.submit(settings)

    def wait(job):
        deadline = time.monotonic() + 300
        while manager.active is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert manager.snapshot(job.id)["status"] == "complete", manager.snapshot(job.id).get(
            "error"
        )

    wait(source)
    original = copy.deepcopy(manager.snapshot(source.id))
    repeated = manager.repeat_checks(source.id)
    wait(repeated)
    report = manager.consistency_checks([source.id, repeated.id])
    assert report["included_replies"] == 2 and not report["excluded_replies"]
    assert len(report["groups"]) == 1 and report["groups"][0]["reply_count"] == 2
    for observation in report["groups"][0]["observations"]:
        response = observation["response"]
        assert response["text"] == response["response_envelope"]["message"]["content"]
        assert (
            response["request_envelope"]["body"]["messages"][-1]["content"]
            == report["groups"][0]["question"]["prompt"]
        )
    assert manager.snapshot(source.id) == original
    (root / "repeat-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


@pytest.mark.parametrize(
    "body,expected",
    [
        (None, None),
        ({}, None),
        ({"options": None}, None),
        ({"options": {"seed": True}}, None),
        ({"options": {"seed": 7}}, 7),
        ({"seed": 9}, 9),
    ],
)
def test_missing_or_invalid_seed_metadata_stays_unknown(body, expected):
    assert recorded_seed({"request_envelope": {"body": body}}) == expected


@pytest.mark.parametrize("kind", ["settings", "question", "missing_question", "duplicate"])
def test_repeat_rejects_changed_or_corrupt_source_before_launch(tmp_path, record, kind):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record, "a" * 32)
    if kind == "settings":
        source.result["settings"]["max_tokens"] = 1024
    elif kind == "question":
        source.result["results"][0]["question"]["expected"] = "changed"
    elif kind == "missing_question":
        source.result["results"][0]["question"] = None
    else:
        source.result["results"].append(copy.deepcopy(source.result["results"][0]))
    with pytest.raises(ValueError):
        manager.repeat_checks(source.id)
    assert len(manager.jobs) == 1 and manager.active is None
