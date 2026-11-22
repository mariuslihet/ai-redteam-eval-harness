"""Projects organize real sources and cannot rewrite measured evidence."""

import json

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_jobs import ModelCheckRequest
from redteam_eval.models import BackendConfig
from redteam_eval.projects import ProjectSaveRequest, ProjectStore


def test_project_round_trip_checklist_archive_and_revision_conflict(tmp_path):
    store = ProjectStore(tmp_path)
    assert store.read()["projects"] == [] and not store.path().exists()
    saved = store.save(
        ProjectSaveRequest(
            title=" Release ",
            tags=["release"],
            checklist=[{"id": "a" * 32, "text": "Inspect actual replies", "done": True}],
        )
    )["project"]
    assert saved["title"] == "Release" and saved["revision"] == 1
    assert ProjectStore(tmp_path).read()["projects"] == [saved]
    before = store.path().read_bytes()
    with pytest.raises(RuntimeError, match="another window"):
        store.save(ProjectSaveRequest(id=saved["id"], expected_revision=0, title="Other"))
    assert store.path().read_bytes() == before
    archived = store.save(
        ProjectSaveRequest(id=saved["id"], expected_revision=1, title="Release", archived=True)
    )["project"]
    assert archived["archived"] and archived["revision"] == 2
    restored = store.save(ProjectSaveRequest(id=saved["id"], expected_revision=2, title="Release"))[
        "project"
    ]
    assert not restored["archived"] and restored["created_at"] == saved["created_at"]


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"suite_ids": ["bad"]},
        {"job_ids": ["a" * 32] * 2},
        {"tags": ["x", " X "]},
        {"status": "passed"},
        {"checklist": [{"id": "a" * 32, "text": " "}]},
        {"suite_ids": [str(i).zfill(32) for i in range(11)]},
    ],
)
def test_invalid_project_data_is_rejected(changes):
    with pytest.raises(ValueError):
        ProjectSaveRequest(**({"title": "Project"} | changes))


def test_corrupt_project_file_is_preserved(tmp_path):
    store = ProjectStore(tmp_path)
    store.path().write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        store.save(ProjectSaveRequest(title="Project"))
    assert store.path().read_text(encoding="utf-8") == "{"


def test_project_api_validates_links_and_captures_project_origin_in_queued_job(
    tmp_path, monkeypatch
):
    app = DashboardApp(tmp_path, BackendConfig())
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }

    def post(route, payload, override=None):
        return app.handle("POST", route, override or headers, json.dumps(payload).encode())

    assert (
        post("/api/projects", {"title": "Project"}, headers | {"X-Dashboard-Token": "bad"}).status
        == 403
    )
    assert post("/api/projects", {"title": "Project", "job_ids": ["b" * 32]}).status == 400
    settings = ModelCheckRequest(
        action="check_model", model="installed", selected_skills=["Arithmetic"]
    ).model_dump(mode="json")
    suite = json.loads(
        post("/api/test-suites", {"title": "Arithmetic", "settings": settings}).body
    )["suite"]
    project = json.loads(
        post("/api/projects", {"title": "Project", "suite_ids": [suite["id"]]}).body
    )["project"]
    assert not app.jobs.jobs
    launch = {
        "id": project["id"],
        "revision": project["revision"],
        "queue_revision": 0,
        "suite_revisions": {suite["id"]: 2},
    }
    assert post("/api/project-queue", launch).status == 409
    launch["suite_revisions"][suite["id"]] = 1
    assert post("/api/project-queue", launch).status == 200
    entry = app.queue.state.items[0]
    assert entry.project_id == project["id"] and entry.request.model_dump(mode="json") == settings

    # Hold scheduling only; no model completion is substituted.
    def hold(job):
        app.jobs.active = job.id
        job.status = "running"
        app.jobs.save(job)

    monkeypatch.setattr(app.jobs, "_start", hold)
    snapshot = app.queue.snapshot()
    assert (
        post(
            "/api/queue/action", {"expected_revision": snapshot["revision"], "action": "start"}
        ).status
        == 200
    )
    job = app.jobs.jobs[app.jobs.active]
    assert (
        job.project_id == project["id"]
        and job.project_revision == 1
        and job.project_title == "Project"
    )
    assert job.request == settings
    assert app.handle("GET", "/assets/project-workspace.mjs", headers).status == 200
