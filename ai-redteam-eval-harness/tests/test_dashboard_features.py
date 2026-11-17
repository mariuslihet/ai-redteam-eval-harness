"""Scorecard and replay API contracts over actual recorded native responses."""

import json
import shutil
from pathlib import Path

import pytest

from redteam_eval.archive import load_archive
from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_jobs import ReplayRequest
from redteam_eval.inspection import inspect_pair
from redteam_eval.models import BackendConfig
from redteam_eval.scorecards import capability_scorecard

RECORDING = Path(__file__).parent / "fixtures" / "measured-qwen-run.json"


def test_scorecard_counts_reconcile_with_independent_case_inspections():
    archive = load_archive(RECORDING)
    report = capability_scorecard(archive)
    assert report["case_count"] == sum(group["cases"] for group in report["groups"]) == 6
    assert sum(report["status_counts"].values()) == 6 * 7 * 2
    for group in report["groups"]:
        assert group["cases"] == group["attacks"] + group["controls"] == 2
        matching = [pair for pair in archive.results if pair.scenario.family == group["family"]]
        for mode in ("baseline", "hardened"):
            rows = [inspect_pair(pair)["modes"][mode]["checks"] for pair in matching]
            assert group["modes"][mode]["failed_cases"] == sum(
                any(c["status"] == "fail" for c in row) for row in rows
            )
            assert group["modes"][mode]["failed_checks"] == sum(
                c["status"] == "fail" for row in rows for c in row
            )
            for counts in group["modes"][mode]["checks"].values():
                assert sum(counts.values()) == 2


def test_unknown_and_inapplicable_checks_never_inflate_pass_counts():
    archive = load_archive(RECORDING)
    for pair in archive.results:
        for trace in (pair.baseline, pair.hardened):
            for turn in trace.turns:
                turn.response.response_envelope.pop("done_reason", None)
                turn.response.finish_reason = None
                turn.response.truncated = False
    report = capability_scorecard(archive)
    assert report["status_counts"]["unknown"] == 12
    for group in report["groups"]:
        for mode in ("baseline", "hardened"):
            assert group["modes"][mode]["checks"]["Reply finished"]["pass"] == 0


@pytest.fixture
def app(tmp_path):
    destination = tmp_path / "source" / "telemetry.json"
    destination.parent.mkdir()
    shutil.copyfile(RECORDING, destination)
    return DashboardApp(tmp_path, BackendConfig())


def test_scorecard_api_cache_and_invalid_source(app):
    identifier = app.store.catalog()["runs"][0]["id"]
    headers = {"Host": "127.0.0.1:8765"}
    response = app.handle("GET", f"/api/runs/{identifier}/scorecard", headers)
    report = json.loads(response.body)
    assert response.status == 200 and report["case_count"] == 6
    assert app.store.scorecard(identifier) is app.store.scorecard(identifier)
    path = app.store.path(identifier)
    path.write_text("{", encoding="utf-8")
    assert app.handle("GET", f"/api/runs/{identifier}/scorecard", headers).status == 400
    assert app.handle("GET", "/api/runs/missing/scorecard", headers).status == 404


def test_replay_captures_source_before_worker_starts(app, monkeypatch):
    # Pause only the scheduler; no fabricated model completion is supplied.
    monkeypatch.setattr(app.jobs, "_start", lambda job: None)
    identifier = app.store.catalog()["runs"][0]["id"]
    job = app.jobs.submit({"action": "replay", "run_id": identifier, "model": "installed-model"})
    snapshot = app.jobs.directory / job.id / "source-telemetry.json"
    assert load_archive(snapshot) == load_archive(RECORDING)
    app.store.path(identifier).write_text("{", encoding="utf-8")
    assert load_archive(snapshot) == load_archive(RECORDING)
    assert app.jobs.snapshot(job.id)["total_pairs"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"max_total_tokens": 0},
        {"max_run_seconds": float("nan")},
        {"run_id": "../source"},
        {"use_judge": False},
        {"guard": {}},
    ],
)
def test_replay_rejects_invalid_allowances_and_scoring_overrides(extra):
    with pytest.raises(ValueError):
        ReplayRequest.model_validate(
            {"action": "replay", "model": "installed-model", "run_id": "run_" + "a" * 24} | extra
        )


def test_replay_missing_source_does_not_create_job(app):
    with pytest.raises(KeyError):
        app.jobs.submit(
            {"action": "replay", "run_id": "run_" + "f" * 24, "model": "installed-model"}
        )
    assert not app.jobs.jobs and app.jobs.active is None
