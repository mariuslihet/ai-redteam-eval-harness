"""Queue state transitions plus opt-in actual inference; no response substitutes."""

import copy
import json
import os
import time
from pathlib import Path

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import JobManager, ModelCheckRequest
from redteam_eval.job_queue import JobQueue, QueueAction
from redteam_eval.models import BackendConfig


def setup(model="installed"):
    return ModelCheckRequest(
        action="check_model", model=model, selected_skills=["Arithmetic"]
    ).model_dump(mode="json")


def definition(title="Arithmetic", settings=None):
    return {"title": title, "settings": settings or setup()}


def make_queue(tmp_path):
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    return manager, JobQueue(manager)


def act(queue, action, **values):
    return queue.action(
        QueueAction(action=action, expected_revision=queue.state.revision, **values)
    )


def test_pending_work_can_be_added_reordered_removed_and_repeated_without_inference(tmp_path):
    manager, queue = make_queue(tmp_path)
    queue.add([definition("A"), definition("B"), definition("C")], 0)
    a, b, c = [item.id for item in queue.state.items]
    act(queue, "move_up", item_id=c)
    assert [item.title for item in queue.state.items] == ["A", "C", "B"]
    act(queue, "move_down", item_id=a)
    assert [item.title for item in queue.state.items] == ["C", "A", "B"]
    act(queue, "cancel", item_id=b)
    assert queue.state.items[-1].status == "cancelled"
    assert queue.state.items[-1].job_id is None
    act(queue, "repeat", item_id=c)
    assert queue.state.items[-1].request == queue.state.items[0].request
    assert queue.state.items[-1].id != c
    assert not manager.jobs and manager.active is None
    act(queue, "clear_finished")
    assert len(queue.state.items) == 3


def test_atomic_batch_validation_limits_and_stale_edits(tmp_path):
    manager, queue = make_queue(tmp_path)
    bad = definition("B", setup() | {"max_tokens": 0})
    with pytest.raises(ValueError):
        queue.add([definition(), bad], 0)
    assert not queue.state.items and not queue.path().exists()
    queue.add([definition(str(i)) for i in range(10)], 0)
    queue.add([definition(str(i)) for i in range(10)], queue.state.revision)
    before = queue.path().read_bytes()
    with pytest.raises(ValueError, match="20 pending"):
        queue.add([definition()], queue.state.revision)
    with pytest.raises(RuntimeError, match="queue changed"):
        queue.add([definition()], 0)
    assert queue.path().read_bytes() == before and not manager.jobs


def test_saved_setups_are_snapshots_and_reopen_paused(tmp_path):
    _, queue = make_queue(tmp_path)
    source = definition()
    queue.add([source], 0)
    source["settings"]["model"] = "changed"
    assert queue.state.items[0].request.model == "installed"
    queue.state.paused = False
    queue._save()
    manager, restored = make_queue(tmp_path)
    assert restored.state.paused and restored.state.items[0].status == "pending"
    assert restored.state.items[0].request.model == "installed" and not manager.jobs


def test_dispatch_is_serial_and_pause_does_not_stop_current_job(tmp_path, monkeypatch):
    manager, queue = make_queue(tmp_path)
    started = []

    # Hold scheduling only; these state probes never construct model replies.
    def hold(job):
        manager.active = job.id
        job.status = "running"
        manager.save(job)
        started.append(job.id)

    monkeypatch.setattr(manager, "_start", hold)
    queue.add([definition("A"), definition("B")], 0)
    act(queue, "start")
    assert len(started) == 1
    first = manager.jobs[started[0]]
    act(queue, "pause")
    assert first.status == "running"
    first.status = "complete"
    manager.active = None
    queue._on_idle(first)
    assert len(started) == 1 and queue.state.items[1].status == "pending"
    act(queue, "start")
    assert len(started) == 2 and manager.active == started[1]


def test_failed_job_pauses_queue_and_stop_policy_can_be_changed(tmp_path, monkeypatch):
    manager, queue = make_queue(tmp_path)

    def hold(job):
        manager.active = job.id
        job.status = "running"
        manager.save(job)

    monkeypatch.setattr(manager, "_start", hold)
    queue.add([definition("A"), definition("B")], 0)
    act(queue, "start")
    first = manager.jobs[manager.active]
    first.status, first.error = "failed", "State-transition probe: worker failure"
    manager.active = None
    queue._on_idle(first)
    assert queue.state.paused and queue.state.items[1].status == "pending"
    act(queue, "set_error_policy", pause_on_error=False)
    act(queue, "start")
    assert queue.state.items[1].status == "running"


@pytest.mark.parametrize("content", ["{", "[" * 257 + "0" + "]" * 257])
def test_corrupt_queue_is_preserved_and_no_work_starts(tmp_path, content):
    (tmp_path / ".job-queue.json").write_text(content, encoding="utf-8")
    manager, queue = make_queue(tmp_path)
    assert queue.snapshot()["load_error"]
    with pytest.raises(ValueError, match="preserved"):
        act(queue, "start")
    assert (tmp_path / ".job-queue.json").read_text(encoding="utf-8") == content
    assert not manager.jobs


def test_suite_batch_api_checks_revisions_and_does_not_run_a_paused_queue(tmp_path):
    app = DashboardApp(tmp_path, BackendConfig())
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }

    def post(path, payload, override=None):
        return app.handle("POST", path, override or headers, json.dumps(payload).encode())

    saved = json.loads(post("/api/test-suites", {"title": "Saved plan", "settings": setup()}).body)[
        "suite"
    ]
    payload = {"expected_revision": 0, "suites": [{"id": saved["id"], "revision": 1}]}
    assert post("/api/queue/suites", payload, headers | {"X-Dashboard-Token": "bad"}).status == 403
    bad = copy.deepcopy(payload)
    bad["suites"][0]["revision"] = 2
    assert post("/api/queue/suites", bad).status == 409
    assert post("/api/queue/suites", payload).status == 200
    snapshot = app.queue.snapshot()
    assert snapshot["paused"] and snapshot["pending_questions"] == 1
    assert not app.jobs.jobs
    assert post("/api/queue/action", {"expected_revision": 0, "action": "start"}).status == 409
    assert app.handle("GET", "/assets/queue-workbench.mjs", headers).status == 200


@pytest.mark.local_llm
def test_actual_queue_runs_serially_pauses_on_missing_model_and_preserves_replies(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_QUEUE_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    queue = JobQueue(manager)
    # Retain existing evidence when re-running this opt-in workflow.
    if queue.state.items:
        pytest.skip("Choose a fresh REDTEAM_QUEUE_LIVE_ROOT for the live queue proof")
    queue.add(
        [
            definition("First actual run", setup(model)),
            definition("Missing installed model", setup("queue-proof-model-not-installed")),
            definition("Next actual run", setup(model)),
        ],
        queue.state.revision,
    )
    assert not manager.jobs
    act(queue, "start")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        snapshot = queue.snapshot()
        if snapshot["paused"] and snapshot["items"][1]["status"] == "failed":
            break
        time.sleep(0.05)
    assert queue.snapshot()["items"][2]["status"] == "pending"
    assert manager.active is None
    act(queue, "start")
    while time.monotonic() < deadline and queue.snapshot()["items"][2]["status"] != "complete":
        time.sleep(0.05)
    snapshot = queue.snapshot()
    assert [item["status"] for item in snapshot["items"]] == ["complete", "failed", "complete"]
    first, last = [manager.jobs[snapshot["items"][i]["job_id"]] for i in [0, 2]]
    for job in [first, last]:
        response = job.result["results"][0]["response"]
        assert response["text"] == response["response_envelope"]["message"]["content"]
        assert response["response_envelope"]["model"] == model
    assert snapshot["items"][0]["finished_at"] <= snapshot["items"][2]["started_at"]
    assert first.id != last.id
    proof = root / "queue-proof.json"
    proof.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def test_stop_current_pauses_dispatch_before_requesting_cancellation(tmp_path, monkeypatch):
    manager, queue = make_queue(tmp_path)

    def hold(job):
        manager.active = job.id
        job.status = "running"
        manager.save(job)

    monkeypatch.setattr(manager, "_start", hold)
    queue.add([definition("A"), definition("B")], 0)
    act(queue, "start")
    active = manager.active
    act(queue, "stop_current")
    assert queue.state.paused and manager.cancel_requested
    assert manager.active == active and queue.state.items[1].status == "pending"


def test_shutdown_always_disarms_dispatch_even_if_queue_file_was_removed(tmp_path):
    manager, queue = make_queue(tmp_path)
    queue.add([definition()], 0)
    queue.state.paused = False
    queue.path().unlink()
    queue.shutdown()
    assert queue.state.paused and queue.path().exists()
    assert not manager.jobs


def test_resumed_job_keeps_its_queue_entry_and_updates_final_status(tmp_path, monkeypatch):
    manager, queue = make_queue(tmp_path)

    def hold(job):
        manager.active = job.id
        job.status = "running"
        manager.save(job)

    monkeypatch.setattr(manager, "_start", hold)
    queue.add([definition()], 0)
    act(queue, "start")
    job = manager.jobs[manager.active]
    job.status = "failed"
    manager.active = None
    queue._on_idle(job)
    assert queue.state.paused
    job.status = "running"
    manager.active = job.id
    act(queue, "clear_finished")
    assert len(queue.state.items) == 1 and queue.snapshot()["items"][0]["status"] == "running"
    job.status = "complete"
    manager.active = None
    queue._on_idle(job)
    assert queue.snapshot()["items"][0]["status"] == "complete"
