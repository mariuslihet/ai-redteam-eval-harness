"""Continuation validation over preserved actual Qwen replies; no completion doubles."""

import copy
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from redteam_eval.budgets import BudgetExceeded, RunBudget
from redteam_eval.checkpoint import atomic_json
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import DashboardJob, JobManager, now
from redteam_eval.models import BackendConfig, Response, RunConfig, Usage


@pytest.fixture
def recorded_skills():
    path = Path(__file__).parent / "fixtures" / "measured-qwen-skills.json"
    return json.loads(path.read_text(encoding="utf-8"))


def stopped_job(tmp_path, record):
    manager = JobManager(ReportStore(tmp_path), BackendConfig(model=record["model"]))
    job = DashboardJob(
        id="f" * 32,
        action="check_model",
        status="cancelled",
        created_at=now(),
        updated_at=now(),
        request=record["settings"],
        result=record,
    )
    manager.jobs[job.id] = job
    manager.save(job)
    atomic_json(manager.directory / job.id / "model-checks.json", record)
    return manager, job


def test_fixture_contains_native_replies_and_is_unchanged(recorded_skills):
    assert (
        hashlib.sha256(
            (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_bytes()
        ).hexdigest()
        == "d3470cd7dd1608f1a175df4d62f280abb770b3cd756a6cfb7bdc3a8805ba3223"
    )
    assert len(recorded_skills["results"]) == 4
    for row in recorded_skills["results"]:
        response = Response.model_validate(row["response"])
        assert response.text == response.response_envelope["message"]["content"]
        assert response.model_digest == recorded_skills["model_digest"]
        assert response.usage.input_tokens > 0


def test_partial_checkpoint_validates_without_inference_or_source_edits(tmp_path, recorded_skills):
    recorded_skills["results"] = recorded_skills["results"][:1]
    manager, job = stopped_job(tmp_path, recorded_skills)
    path = manager.directory / job.id / "model-checks.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert manager._skill_checkpoint(job) == recorded_skills
    assert manager.snapshot(job.id)["can_continue"]
    assert manager.snapshot(job.id)["remaining_checks"] == 3
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    restored = JobManager(manager.store, manager.backend)
    assert restored._skill_checkpoint(restored.jobs[job.id]) == recorded_skills


@pytest.mark.parametrize(
    "damage",
    [
        "question",
        "expected_type",
        "order",
        "duplicate",
        "model",
        "seed",
        "settings",
        "grader",
        "digest",
        "usage",
        "count",
    ],
)
def test_changed_checkpoints_are_rejected_before_worker_starts(tmp_path, recorded_skills, damage):
    record = copy.deepcopy(recorded_skills)
    if damage == "question":
        record["results"][0]["question"]["expected"] += 1
    elif damage == "expected_type":
        record["results"][0]["question"]["expected"] = float(
            record["results"][0]["question"]["expected"]
        )
    elif damage == "order":
        record["results"][:2] = reversed(record["results"][:2])
    elif damage == "duplicate":
        record["results"][1] = record["results"][0]
    elif damage == "model":
        record["model"] = "another-model"
    elif damage == "seed":
        record["seed"] += 1
    elif damage == "settings":
        record["settings"]["cases_per_skill"] = 3
    elif damage == "grader":
        record["grader_version"] = 0
    elif damage == "digest":
        record["results"][0]["response"]["model_digest"] = "changed"
    elif damage == "usage":
        del record["results"][0]["response"]["usage"]
    else:
        record["results"] *= 2
    manager, job = stopped_job(tmp_path, record)
    job.request = recorded_skills["settings"]
    with pytest.raises(ValueError):
        manager.resume(job.id)
    assert manager.active is None and job.status == "cancelled"


def test_completed_or_busy_skill_run_cannot_continue(tmp_path, recorded_skills):
    manager, job = stopped_job(tmp_path, recorded_skills)
    assert not manager.snapshot(job.id)["can_continue"]
    with pytest.raises(ValueError, match="All questions"):
        manager.resume(job.id)
    manager.active = "another-job"
    with pytest.raises(RuntimeError, match="idle"):
        manager.resume(job.id)


@pytest.mark.parametrize("estimated", [False, True])
def test_saved_usage_cannot_bypass_token_limits(tmp_path, recorded_skills, estimated):
    record = recorded_skills
    record["results"] = record["results"][:1]
    record["settings"]["max_total_tokens"] = 1
    record["results"][0]["response"]["usage"]["estimated"] = estimated
    manager, job = stopped_job(tmp_path, record)
    with pytest.raises(BudgetExceeded):
        manager.resume(job.id)
    assert manager.active is None


def test_restored_response_usage_is_separate_from_fresh_calls():
    budget = RunBudget(RunConfig(max_total_tokens=100))
    budget.restore_response(Usage(input_tokens=10, output_tokens=5))
    assert budget.record.total_tokens == budget.record.restored_tokens == 15
    assert budget.record.restored_responses == 1
    assert budget.record.model_responses == budget.record.request_attempts == 0
    budget.before_request()
    budget.observe(Usage(input_tokens=20, output_tokens=5))
    assert budget.record.total_tokens == 40
    assert budget.record.observed_tokens == 25


def test_exactly_exhausted_allowance_does_not_start_another_request(tmp_path, recorded_skills):
    record = recorded_skills
    record["results"] = record["results"][:1]
    usage = record["results"][0]["response"]["usage"]
    record["settings"]["max_total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    manager, job = stopped_job(tmp_path, record)
    with pytest.raises(ValueError, match="exhausted"):
        manager.resume(job.id)
    assert manager.active is None


async def test_changed_installed_model_is_rejected_before_inference(
    tmp_path, recorded_skills, monkeypatch
):
    recorded_skills["results"] = recorded_skills["results"][:1]
    manager, job = stopped_job(tmp_path, recorded_skills)

    async def changed_inventory(name):
        return BackendConfig(model=name, expected_digest="a" * 64)

    # Only inventory metadata is replaced; no model completion is invented.
    monkeypatch.setattr(manager, "selected_backend", changed_inventory)
    with pytest.raises(ValueError, match="installed model version changed"):
        await manager._check_model(job)
    assert manager.budget is None


@pytest.mark.local_llm
def test_actual_cancel_restart_and_continue_preserves_replies_and_charges_usage(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_CONTINUE_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    job = manager.submit(
        {
            "action": "check_model",
            "model": model,
            "test_suite": "extended",
            "selected_skills": ["Arithmetic", "Planning dependent tasks"],
            "cases_per_skill": 3,
            "seed": 20261118,
        }
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(job.id)
        if snapshot["completed_checks"] > 0 or snapshot["status"] == "failed":
            break
        time.sleep(0.01)
    assert snapshot["status"] != "failed", snapshot.get("error")
    manager.cancel(job.id)
    while manager.active is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    stopped = manager.snapshot(job.id)
    assert stopped["status"] == "cancelled"
    preserved = copy.deepcopy(stopped["result"]["results"])
    assert 0 < len(preserved) < 6
    restored = JobManager(ReportStore(root), BackendConfig(model=model))
    restored.resume(job.id)
    while restored.active is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    done = restored.snapshot(job.id)
    assert done["status"] == "complete", done.get("error")
    assert done["completed_checks"] == done["total_checks"] == 6
    assert done["result"]["results"][: len(preserved)] == preserved
    assert len({item["question"]["id"] for item in done["result"]["results"]}) == 6
    assert done["execution"]["model_responses"] == 6 - len(preserved)
    assert done["execution"]["restored_responses"] == len(preserved)
    assert done["execution"]["restored_tokens"] == sum(
        item["response"]["usage"]["input_tokens"] + item["response"]["usage"]["output_tokens"]
        for item in preserved
    )
    assert done["result"]["continuations"] == 1
    assert done["result"]["restored_checks"] == len(preserved)
    assert not done["can_continue"]
