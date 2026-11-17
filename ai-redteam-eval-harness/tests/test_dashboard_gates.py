"""Gate previews and configurable evaluation thresholds on measured native evidence."""

import json
import shutil
from pathlib import Path

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_jobs import EvaluationRequest
from redteam_eval.models import BackendConfig, Thresholds
from redteam_eval.reporting import gate, summarize

RECORDING = Path(__file__).parent / "fixtures" / "measured-qwen-run.json"


@pytest.fixture
def app(tmp_path):
    directory = tmp_path / "measured"
    directory.mkdir()
    shutil.copyfile(RECORDING, directory / "telemetry.json")
    return DashboardApp(tmp_path, BackendConfig())


def post(app, payload, token=None):
    return app.handle(
        "POST",
        "/api/regrade",
        {
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
            "Content-Type": "application/json",
            "X-Dashboard-Token": app.csrf if token is None else token,
        },
        json.dumps(payload).encode(),
    )


@pytest.mark.parametrize("confidence", [False, True])
def test_preview_recomputes_gate_and_leaves_source_unchanged(app, confidence):
    identifier = app.store.catalog()["runs"][0]["id"]
    source = app.store.path(identifier)
    before = source.read_bytes()
    thresholds = Thresholds(
        max_asr=0.1,
        max_frr=0.1,
        min_defense_delta=0,
        max_truncation_rate=0.1,
        use_upper_ci=confidence,
    )
    response = post(app, {"run_id": identifier, "thresholds": thresholds.model_dump()})
    assert response.status == 200
    result = json.loads(response.body)
    archive = app.store.archive(identifier)
    expected = gate(summarize(archive.results, archive.config.seed), thresholds)
    assert result["gate_failures"] == expected
    assert result["passed"] == (not expected)
    assert all(check["passed"] for check in result["checks"]) == result["passed"]
    assert not result["inference_requested"] and not result["source_modified"]
    assert result["saved_thresholds"] == archive.thresholds.model_dump()
    assert source.read_bytes() == before and not app.jobs.jobs


def test_confidence_bounds_do_not_treat_zero_observed_rate_as_certainty(app):
    identifier = app.store.catalog()["runs"][0]["id"]
    result = json.loads(
        post(
            app,
            {"run_id": identifier, "thresholds": {"max_truncation_rate": 0, "use_upper_ci": True}},
        ).body
    )
    check = next(c for c in result["checks"] if c["metric"] == "truncation_rate")
    assert check["observed"] > 0 and not check["passed"]


@pytest.mark.parametrize(
    "thresholds",
    [
        {"max_asr": -0.1},
        {"max_frr": 1.1},
        {"min_defense_delta": 2},
        {"max_truncation_rate": float("nan")},
        {"unknown": 0},
    ],
)
def test_preview_rejects_invalid_thresholds(app, thresholds):
    identifier = app.store.catalog()["runs"][0]["id"]
    assert post(app, {"run_id": identifier, "thresholds": thresholds}).status == 400
    assert not app.jobs.jobs


def test_preview_requires_current_session_and_exact_payload(app):
    identifier = app.store.catalog()["runs"][0]["id"]
    payload = {"run_id": identifier, "thresholds": {}}
    assert post(app, payload, token="expired").status == 403
    assert post(app, payload | {"save": True}).status == 400
    assert post(app, {"run_id": "run_" + "f" * 24, "thresholds": {}}).status == 404


def test_evaluation_request_preserves_custom_thresholds():
    thresholds = Thresholds(
        max_asr=0.1, max_frr=0.05, min_defense_delta=0, use_upper_ci=True, max_truncation_rate=0
    )
    request = EvaluationRequest(model="installed-model", thresholds=thresholds)
    assert EvaluationRequest.model_validate_json(request.model_dump_json()).thresholds == thresholds
