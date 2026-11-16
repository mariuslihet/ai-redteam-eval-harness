"""Dashboard regressions against a recorded, actual native-inference archive."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from redteam_eval.archive import load_archive
from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_data import ReportStore, artifact_id, native_evidence
from redteam_eval.dashboard_jobs import (
    CaseAnalysis,
    DashboardJob,
    EvaluationRequest,
    JobManager,
    analysis_schema,
    native_turns,
    validate_analysis,
)
from redteam_eval.models import BackendConfig
from redteam_eval.reporting import summarize

RECORDING = Path(__file__).parent / "fixtures" / "measured-qwen-run.json"


@pytest.fixture
def store(tmp_path):
    destination = tmp_path / "measured" / "telemetry.json"
    destination.parent.mkdir()
    shutil.copyfile(RECORDING, destination)
    return ReportStore(tmp_path)


@pytest.fixture
def app(store):
    return DashboardApp(store.root, BackendConfig())


def headers(app):
    return {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "X-Dashboard-Token": app.csrf,
        "Content-Type": "application/json",
    }


def test_recording_provenance():
    assert hashlib.sha256(RECORDING.read_bytes()).hexdigest() == (
        "896b2a0a39333e2fa65774382a1d1ef637082211d4a717845cc5fa7754b196d0"
    )
    archive = load_archive(RECORDING)
    assert native_evidence(archive) == sum(
        len(trace.turns) for pair in archive.results for trace in (pair.baseline, pair.hardened)
    )


def test_catalog_recomputes_cached_metrics_and_preserves_native_output(store):
    path = store.root / "measured" / "telemetry.json"
    value = json.loads(path.read_text())
    value["summary"] = {"stale": True}
    path.write_text(json.dumps(value), encoding="utf-8")
    catalog = store.catalog()
    assert catalog["status"] == "ready"
    item = catalog["runs"][0]
    archive = load_archive(RECORDING)
    assert item["summary"] == summarize(archive.results, archive.config.seed)
    detail = store.detail(item["id"])
    assert detail["results"] == archive.model_dump(mode="json")["results"]
    assert store.download(item["id"], "telemetry.json") == path.read_bytes()


@pytest.mark.parametrize("corruption", ["native", "model", "incomplete", "malformed"])
def test_unmeasured_and_invalid_archives_are_explicitly_excluded(store, corruption):
    path = store.root / "excluded" / "telemetry.json"
    path.parent.mkdir()
    value = json.loads(RECORDING.read_text())
    response = value["results"][0]["baseline"]["turns"][0]["response"]
    if corruption == "native":
        response["response_envelope"] = {}
    elif corruption == "model":
        response["response_envelope"]["model"] = "local-simulated"
    elif corruption == "incomplete":
        value["results"].pop()
    path.write_text("{" if corruption == "malformed" else json.dumps(value), encoding="utf-8")
    catalog = store.catalog()
    assert len(catalog["runs"]) == len(catalog["issues"]) == 1
    assert catalog["status"] == "partial"
    with pytest.raises(KeyError):
        store.archive(artifact_id("excluded/telemetry.json"))


def test_paths_download_allowlist_and_missing_identifiers(store, tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        store.bounded_path(tmp_path.parent / "secret.json")
    identifier = store.catalog()["runs"][0]["id"]
    for filename in ("../telemetry.json", "job.json", "analysis.json"):
        with pytest.raises(ValueError, match="not available"):
            store.download(identifier, filename)
    with pytest.raises(KeyError):
        store.detail("run_" + "f" * 24)


def test_incomplete_campaign_recomputes_seed_and_never_claims_pooled_metrics(store):
    from redteam_eval.budgets import ExecutionRecord
    from redteam_eval.campaign import CampaignRecord, SeedRecord

    archive = load_archive(RECORDING)
    campaign = CampaignRecord(
        config=archive.config,
        seeds=[1729, 42],
        thresholds=archive.thresholds,
        status="failed",
        summary={"stale": True},
        seed_results=[
            SeedRecord(
                seed=1729,
                report_directory="measured",
                summary={"stale": True},
                gate_failures=[],
                execution=ExecutionRecord(),
            )
        ],
    )
    path = store.root / "campaign.json"
    path.write_text(campaign.model_dump_json(), encoding="utf-8")
    catalog = store.catalog()
    measured = catalog["campaigns"][0]
    assert measured["summary"] is None and measured["status"] == "failed"
    assert measured["seed_results"][0]["summary"] == summarize(archive.results, 1729)
    campaign.seed_results[0].seed = 42
    path.write_text(campaign.model_dump_json(), encoding="utf-8")
    assert not store.catalog()["campaigns"]


def test_invalid_campaign_repetition_is_excluded(store):
    from redteam_eval.campaign import CampaignRecord

    archive = load_archive(RECORDING)
    campaign = CampaignRecord(
        config=archive.config, seeds=[1729, 1729], thresholds=archive.thresholds
    )
    (store.root / "campaign.json").write_text(campaign.model_dump_json(), encoding="utf-8")
    assert not store.catalog()["campaigns"]
    assert "distinct seeds" in store.catalog()["issues"][0]["reason"]


def test_api_archive_and_session(app):
    request_headers = headers(app)
    page = app.handle("GET", "/", request_headers)
    assert page.status == 200 and app.csrf.encode() in page.body
    catalog = json.loads(app.handle("GET", "/api/catalog", request_headers).body)
    identifier = catalog["runs"][0]["id"]
    reply = app.handle("GET", f"/api/runs/{identifier}", request_headers)
    assert reply.status == 200 and len(json.loads(reply.body)["results"]) == 6
    assert app.handle("GET", "/assets/dashboard.mjs", request_headers).status == 200
    assert app.handle("GET", "/assets/other.js", request_headers).status == 404
    assert app.handle("GET", "/api/jobs/missing", request_headers).status == 404
    archive = app.store.archive(identifier)
    inspection = app.handle(
        "GET", f"/api/runs/{identifier}/cases/{archive.results[0].key}", request_headers
    )
    assert inspection.status == 200
    assert len(json.loads(inspection.body)["modes"]["baseline"]["checks"]) == 7
    assert app.handle("GET", f"/api/runs/{identifier}/cases/missing", request_headers).status == 404


@pytest.mark.parametrize(
    "change",
    [
        {"Host": "attacker.test"},
        {"Origin": "https://attacker.test"},
        {"Origin": "null"},
        {"X-Dashboard-Token": "expired"},
    ],
)
def test_cross_origin_and_session_rejected_before_inference(app, change):
    request_headers = headers(app) | change
    reply = app.handle("POST", "/api/jobs", request_headers, b"{}")
    assert reply.status == 403
    assert not app.jobs.jobs


@pytest.mark.parametrize(
    "path", ["/../job.json", "/%2e%2e/job.json", "/api\\jobs", "/api/jobs?x=1", "http://["]
)
def test_api_path_traversal(app, path):
    assert app.handle("GET", path, headers(app)).status == 400


@pytest.mark.parametrize(
    "body", [b"[]", b"{", b'{"model":"x","max_turns":0}', b'{"model":"x","extra":1}']
)
def test_invalid_jobs_do_not_start_worker(app, body):
    assert app.handle("POST", "/api/jobs", headers(app), body).status == 400
    assert app.jobs.active is None and not app.jobs.jobs


def test_http_limits_and_methods(app):
    assert app.handle("PUT", "/api/jobs", headers(app)).status == 405
    assert app.handle("POST", "/api/jobs", headers(app), b"x" * 65537).status == 413
    assert (
        app.handle("POST", "/api/jobs", headers(app) | {"Content-Type": "text/plain"}, b"{}").status
        == 415
    )


def test_compare_actual_run_to_itself(app):
    identifier = app.store.catalog()["runs"][0]["id"]
    reply = app.handle(
        "POST",
        "/api/compare",
        headers(app),
        json.dumps({"previous": identifier, "current": identifier}).encode(),
    )
    result = json.loads(reply.body)
    assert reply.status == 200 and not result["gate_failures"]
    assert all(metric["change"]["delta"] == 0 for metric in result["metrics"].values())


def test_analysis_grounding_uses_only_observed_turn_and_native_quote():
    pair = load_archive(RECORDING).results[0]
    turns = native_turns(pair)
    expected_turn_count = len(pair.baseline.turns) + len(pair.hardened.turns)
    assert len(turns) == expected_turn_count
    turn = next(t for t in turns.values() if t["native_text"].strip())
    # This constructs validator input, not a model response or displayed analysis.
    analysis = CaseAnalysis(
        summary="Validator test input for quote grounding against an actual measured output. This is only validator input and is never used as a model completion.",
        **{
            key: "Validator input only; no model inference or displayed explanation is supplied here."
            for key in (
                "baseline_assessment",
                "hardened_assessment",
                "security_impact",
                "utility_tradeoff",
            )
        },
        observations=[
            dict(
                mode=turn["mode"],
                turn_id=turn["turn_id"],
                kind="native_text",
                quote=turn["native_text"][:40],
                claim="Grounding validation input",
            )
        ],
        recommendations=["Validator input"],
        limitations=["No inference is performed in this unit test"],
    )
    validate_analysis(analysis, pair)
    analysis.observations[0].quote = "THIS QUOTE DOES NOT EXIST IN THE OBSERVED OUTPUT"
    with pytest.raises(ValueError, match="verbatim"):
        validate_analysis(analysis, pair)
    analysis.observations[0].turn_id = "unknown-turn"
    with pytest.raises(ValueError, match="unknown"):
        validate_analysis(analysis, pair)
    analysis.observations = []
    with pytest.raises(ValueError, match="did not cite"):
        validate_analysis(analysis, pair)


def test_analysis_schema_citations_are_excerpts_of_real_native_evidence():
    from jsonschema import Draft202012Validator

    for pair in load_archive(RECORDING).results:
        schema, excerpts = analysis_schema(pair)
        Draft202012Validator.check_schema(schema)
        turns = native_turns(pair)
        for item in excerpts:
            turn = turns[item["turn_id"]]
            assert turn["mode"] == item["mode"]
            observed = (
                turn["native_text"]
                if item["kind"] == "native_text"
                else json.dumps(turn["tool_calls"])
            )
            assert all(quote in observed for quote in item["available_quotes"])


def test_interrupted_job_is_failed_and_cannot_be_cancelled(store):
    manager = JobManager(store, BackendConfig())
    job = DashboardJob(
        id="a" * 32,
        action="evaluate",
        status="running",
        created_at="2026-11-16T00:00:00Z",
        updated_at="2026-11-16T00:00:00Z",
        request=EvaluationRequest(model="installed-model").model_dump(),
    )
    manager.save(job)
    reloaded = JobManager(store, BackendConfig())
    assert reloaded.snapshot(job.id)["status"] == "failed"
    assert "restarted" in reloaded.jobs[job.id].error
    assert reloaded.snapshot(job.id)["completed_pairs"] == 0
    with pytest.raises(RuntimeError, match="not running"):
        reloaded.cancel(job.id)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, 3601])
def test_evaluation_rejects_invalid_allowances(value):
    with pytest.raises(ValueError):
        EvaluationRequest(model="installed-model", max_run_seconds=value)
