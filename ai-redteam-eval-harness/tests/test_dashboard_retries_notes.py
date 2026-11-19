"""Retry actual recorded failures and attach separate, conflict-aware review notes."""

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
from redteam_eval.model_checks import review_skill_results
from redteam_eval.models import BackendConfig, Response
from redteam_eval.review_notes import NoteRequest
from redteam_eval.skill_tools import build_skill_plan


@pytest.fixture
def record():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text(
            encoding="utf-8"
        )
    )


def register(manager, record):
    job = DashboardJob(
        id="a" * 32,
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


@pytest.mark.parametrize("ids", [[], ["skill-0-0", "skill-0-0"], ["unknown"], ["challenging-0-0"]])
def test_invalid_exact_question_selections_are_rejected(ids):
    with pytest.raises(ValueError):
        ModelCheckRequest(action="check_model", model="installed", question_ids=ids)


def test_retry_plan_selects_current_confirmed_failures_without_touching_source(
    tmp_path, record, monkeypatch
):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record)
    before = (manager.directory / source.id / "job.json").read_bytes()
    # Only suppress thread startup to inspect the planned work; no completion is fabricated.
    monkeypatch.setattr(manager, "_start", lambda job: None)
    retry = manager.retry_checks(source.id)
    failed = [
        row
        for row in review_skill_results(record)["results"]
        if row["grade"]["status"] in {"wrong_answer", "format_error", "unexpected_tool"}
    ]
    request = ModelCheckRequest.model_validate(retry.request)
    assert request.retry_of == source.id
    assert request.question_count() == len(failed) == 3
    assert [q.model_dump(mode="json") for q in request.questions()] == [
        row["question"] for row in failed
    ]
    assert build_skill_plan(request.model_dump(mode="json"))["question_count"] == 3
    assert manager.snapshot(retry.id)["total_checks"] == 3
    assert (manager.directory / source.id / "job.json").read_bytes() == before
    assert source.result == record


def test_unconfirmed_and_currently_passing_answers_are_not_retried(tmp_path, record):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record)
    source.result["results"] = source.result["results"][:1]
    source.result["results"][0]["grade"]["passed"] = False
    with pytest.raises(ValueError, match="No confirmed"):
        manager.retry_checks(source.id)
    source.result["results"][0]["response"]["finish_reason"] = "length"
    with pytest.raises(ValueError, match="No confirmed"):
        manager.retry_checks(source.id)
    assert manager.active is None and len(manager.jobs) == 1


def test_forged_retry_lineage_and_changed_questions_are_rejected(tmp_path, record):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record)
    settings = ModelCheckRequest.model_validate(source.request).model_dump(mode="json")
    settings.update(retry_of=source.id, question_ids=["skill-0-0"])
    with pytest.raises(ValueError, match="confirmed failed"):
        manager.submit(settings)
    settings.update(seed=settings["seed"] + 1, question_ids=["skill-0-1"])
    with pytest.raises(ValueError, match="exactly match"):
        manager.submit(settings)
    assert manager.active is None and len(manager.jobs) == 1


def test_notes_are_separate_versioned_and_persistent_without_regrading(tmp_path, record):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record)
    path = manager.directory / source.id / "job.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    qid = record["results"][0]["question"]["id"]
    assert manager.notes(source.id)["notes"] == {}
    assert not (path.parent / "review-notes.json").exists()
    saved = manager.notes(
        source.id,
        NoteRequest(
            question_id=qid,
            decision="checker_issue",
            note="Human review input; does not alter the automated score.",
        ),
    )
    assert saved["notes"][qid]["revision"] == 1 and not saved["notes"][qid]["stale"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert source.result == record
    restored = JobManager(ReportStore(tmp_path), BackendConfig())
    assert restored.notes(source.id)["notes"] == saved["notes"]
    with pytest.raises(RuntimeError, match="another session"):
        restored.notes(
            source.id, NoteRequest(question_id=qid, note="would overwrite stale revision")
        )
    updated = restored.notes(
        source.id,
        NoteRequest(
            question_id=qid, decision="pending", note="Updated human review", expected_revision=1
        ),
    )
    assert updated["notes"][qid]["revision"] == 2
    restored.jobs[source.id].result["results"][0]["response"]["text"] += " changed evidence"
    assert restored.notes(source.id)["notes"][qid]["stale"]
    assert manager.active is None


@pytest.mark.parametrize(
    "payload",
    [
        {"question_id": "unknown"},
        {"question_id": "skill-0-0", "decision": "override_score"},
        {"question_id": "skill-0-0", "note": "x" * 4001},
        {"question_id": "skill-0-0", "expected_revision": -1},
    ],
)
def test_invalid_notes_cannot_create_or_override_scores(tmp_path, record, payload):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record)
    with pytest.raises(ValueError):
        manager.notes(source.id, NoteRequest.model_validate(payload))
    assert not (manager.directory / source.id / "review-notes.json").exists()
    assert source.result == record


def test_corrupt_notes_are_not_silently_overwritten(tmp_path, record):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    source = register(manager, record)
    path = manager.directory / source.id / "review-notes.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.notes(source.id, NoteRequest(question_id="skill-0-0"))
    assert path.read_text(encoding="utf-8") == "{"


def test_note_and_retry_endpoints_validate_tokens_revisions_and_payloads(
    tmp_path, record, monkeypatch
):
    app = DashboardApp(tmp_path, BackendConfig())
    source = register(app.jobs, record)
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    payload = json.dumps({"question_id": "skill-0-0", "note": "API note"}).encode()
    route = f"/api/jobs/{source.id}/notes"
    assert app.handle("POST", route, headers | {"X-Dashboard-Token": "bad"}, payload).status == 403
    assert app.handle("POST", route, headers, payload).status == 200
    assert app.handle("POST", route, headers, payload).status == 409
    assert json.loads(app.handle("GET", route, headers).body)["notes"]["skill-0-0"]["revision"] == 1
    assert (
        app.handle("POST", f"/api/jobs/{source.id}/retry", headers, b'{"responses":[]}').status
        == 400
    )
    monkeypatch.setattr(app.jobs, "_start", lambda job: None)
    assert app.handle("POST", f"/api/jobs/{source.id}/retry", headers, b"{}").status == 202


@pytest.mark.local_llm
def test_actual_retry_only_calls_failed_saved_questions_and_keeps_source(tmp_path, record):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_RETRY_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    source = register(manager, record)
    path = manager.directory / source.id / "job.json"
    original = path.read_bytes()
    retry = manager.retry_checks(source.id, model)
    deadline = time.monotonic() + 300
    while manager.active is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    saved = manager.snapshot(retry.id)
    assert saved["status"] == "complete", saved.get("error")
    assert saved["completed_checks"] == saved["total_checks"] == 3
    assert saved["execution"]["model_responses"] == 3
    assert sum(row["planned"] for row in saved["result"]["summary"]) == 3
    for item in saved["result"]["results"]:
        old = next(
            row for row in record["results"] if row["question"]["id"] == item["question"]["id"]
        )
        assert item["question"] == old["question"]
        response = Response.model_validate(item["response"])
        assert response.text == response.response_envelope["message"]["content"]
        assert response.response_envelope["model"] == model
    assert saved["result"]["retry_source"]["job_id"] == source.id
    assert path.read_bytes() == original and source.result == record
    assert manager._skill_checkpoint(manager.jobs[retry.id]) == saved["result"]
    assert manager.compare_checks(source.id, retry.id)["matched_count"] == 3
