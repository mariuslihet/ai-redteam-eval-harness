"""Opt-in end-to-end dashboard worker checks using actual installed inference."""

import os
import shutil
import time
from pathlib import Path

import pytest

from redteam_eval.dashboard_data import ReportStore, native_evidence
from redteam_eval.dashboard_jobs import CaseAnalysis, JobManager, validate_analysis
from redteam_eval.models import BackendConfig


def wait_for(manager, identifier, predicate, seconds=360):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        snapshot = manager.snapshot(identifier)
        if predicate(snapshot):
            return snapshot
        time.sleep(0.1)
    pytest.fail(f"Live dashboard worker did not reach expected state: {snapshot}")


@pytest.mark.local_llm
def test_dashboard_real_evaluation_cancel_resume_and_grounded_analysis(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL to opt into installed local hardware")
    root = Path(os.getenv("REDTEAM_DASHBOARD_LIVE_ROOT", str(tmp_path)))
    store = ReportStore(root)
    manager = JobManager(store, BackendConfig(model=model))
    job = manager.submit(
        dict(
            model=model,
            seed=1729,
            max_turns=1,
            use_judge=False,
            max_tokens=512,
            max_run_seconds=300,
        )
    )
    first = wait_for(
        manager,
        job.id,
        lambda s: s["completed_pairs"] >= 1 or s["status"] in ("complete", "failed"),
    )
    assert first["status"] != "failed", first["error"]
    if first["status"] != "complete":
        manager.cancel(job.id)
        stopped = wait_for(manager, job.id, lambda s: s["status"] == "cancelled")
        committed = stopped["completed_pairs"]
        assert committed > 0
        manager.resume(job.id)
    else:
        committed = 0
    done = wait_for(manager, job.id, lambda s: s["status"] in ("complete", "failed"))
    assert done["status"] == "complete", done["error"]
    assert done["completed_pairs"] == 6
    assert done["execution"]["restored_pairs"] >= committed
    archive = store.archive(done["result_id"])
    assert native_evidence(archive) == 12
    assert done["execution"]["total_tokens"] > 0

    # Exercise the analyst on the existing real recording, with no judge/model doubles.
    source = root / "measured-source" / "telemetry.json"
    source.parent.mkdir(exist_ok=True)
    shutil.copyfile(Path(__file__).parent / "fixtures" / "measured-qwen-run.json", source)
    identifier = next(
        r["id"]
        for r in store.catalog()["runs"]
        if r["relative_path"] == "measured-source/telemetry.json"
    )
    pair = store.archive(identifier).results[0]
    analysis_job = manager.submit(
        dict(action="analyze", model=model, run_id=identifier, pair_key=pair.key)
    )
    result = wait_for(
        manager, analysis_job.id, lambda s: s["status"] in ("complete", "failed"), seconds=200
    )
    assert result["status"] == "complete", result["error"]
    analysis = CaseAnalysis.model_validate(result["result"]["analysis"])
    validate_analysis(analysis, pair)
    assert result["result"]["native_outputs"]
    assert result["result"]["model_digest"]
