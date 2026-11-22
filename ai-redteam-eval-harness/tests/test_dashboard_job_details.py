"""Human labels and references must not modify measured model evidence."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_jobs import DashboardJob, now
from redteam_eval.job_details import DetailsRequest, JobDetailsStore, ReferenceRequest
from redteam_eval.models import BackendConfig


def test_details_persist_and_reference_is_independent_of_archive(tmp_path):
    store = JobDetailsStore(tmp_path)
    assert store.read()["revision"] == 0 and not store.path().exists()
    data = store.save(
        DetailsRequest(
            job_id="a" * 32,
            expected_revision=0,
            title=" Release check ",
            tags=["release"],
            note="Review the original answer",
            favorite=True,
        )
    )
    assert data["records"]["a" * 32]["title"] == "Release check"
    data = store.reference(ReferenceRequest(job_id="a" * 32, expected_revision=1))
    data = store.save(DetailsRequest(job_id="a" * 32, expected_revision=2, archived=True))
    assert data["reference_job_id"] == "a" * 32 and data["records"]["a" * 32]["archived"]
    assert JobDetailsStore(tmp_path).read() == data
    assert store.reference(ReferenceRequest(expected_revision=3))["reference_job_id"] is None


def test_stale_saves_preserve_file_and_draft_input(tmp_path):
    store = JobDetailsStore(tmp_path)
    store.save(DetailsRequest(job_id="a" * 32, expected_revision=0, title="Saved"))
    before = store.path().read_bytes()
    draft = DetailsRequest(job_id="a" * 32, expected_revision=0, title="Unsaved draft")
    with pytest.raises(RuntimeError, match="another window"):
        store.save(draft)
    assert draft.title == "Unsaved draft" and store.path().read_bytes() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"tags": ["release", " RELEASE "]},
        {"tags": [""]},
        {"tags": ["x" * 25]},
        {"note": "x" * 4001},
        {"title": "x" * 101},
    ],
)
def test_bad_metadata_is_rejected(changes):
    with pytest.raises(ValueError):
        DetailsRequest(job_id="a" * 32, expected_revision=0, **changes)


@pytest.mark.parametrize(
    "content", ["{", '{"format_version":1,"format_version":2}', "[" * 257 + "0" + "]" * 257]
)
def test_corrupt_details_are_preserved(tmp_path, content):
    store = JobDetailsStore(tmp_path)
    store.path().write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        store.save(DetailsRequest(job_id="a" * 32, expected_revision=0, title="New"))
    assert store.path().read_text(encoding="utf-8") == content


def test_details_api_changes_only_metadata_and_reference_needs_real_saved_rows(tmp_path):
    record = json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text()
    )
    app = DashboardApp(tmp_path, BackendConfig())
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
    before = copy.deepcopy(app.jobs.snapshot(job.id))
    path = app.jobs.directory / job.id / "job.json"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }

    def post(route, payload, override=None):
        return app.handle("POST", route, override or headers, json.dumps(payload).encode())

    payload = {"job_id": job.id, "expected_revision": 0, "title": "Real recorded Qwen"}
    assert post("/api/job-details", payload, headers | {"X-Dashboard-Token": "bad"}).status == 403
    assert post("/api/job-details", payload).status == 200
    assert post("/api/job-details", payload).status == 409
    assert post("/api/job-reference", {"job_id": job.id, "expected_revision": 1}).status == 200
    assert (
        post("/api/job-details", payload | {"job_id": "b" * 32, "expected_revision": 2}).status
        == 400
    )
    assert (
        app.jobs.snapshot(job.id) == before
        and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    )
    assert app.jobs.active is None
    job.status = "running"
    assert post("/api/job-reference", {"job_id": job.id, "expected_revision": 2}).status == 400
