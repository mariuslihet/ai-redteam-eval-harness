import csv
import json

import pytest
from pydantic import ValidationError

from redteam_eval.audit import csv_text, review_cases
from redteam_eval.backends import make_backend
from redteam_eval.cli import main
from redteam_eval.models import (
    BackendConfig,
    ProviderProfile,
    Request,
    Response,
    RunConfig,
    Thresholds,
)
from redteam_eval.providers import ProviderSimulator
from redteam_eval.reporting import response_gallery, write_reports
from redteam_eval.runner import Runner
from tests.doubles import DeterministicBackend


def test_application_has_no_canned_backend():
    with pytest.raises(ValidationError):
        BackendConfig(kind="test")
    with pytest.raises(SystemExit) as exc:
        main(["--backend", "test"])
    assert exc.value.code == 2
    assert type(make_backend(BackendConfig())).__name__ == "HTTPBackend"
    assert RunConfig().providers == [ProviderProfile(name="direct")]


async def test_direct_mode_preserves_native_response_without_provider_simulation():
    response = Response(text="observed", response_envelope={"model": "local", "eval_count": 17})
    backend = DeterministicBackend(script=[response])
    result = await ProviderSimulator(backend, ProviderProfile(name="direct")).generate(
        Request(messages=[])
    )
    assert result == response
    assert result.response_envelope == {"model": "local", "eval_count": 17}
    with pytest.raises(ValidationError):
        ProviderProfile(name="direct", jitter_ms=1)
    with pytest.raises(ValidationError):
        ProviderProfile(name="direct", rate_limit_probability=0.1)


async def test_review_exports_include_turn_evidence_and_escape_html(tmp_path):
    config = RunConfig(cases_per_family=1, max_turns=2)
    results = await Runner(config).run()
    results[0].baseline.turns[0].response.text = '<script>alert("bad")</script>'
    write_reports(tmp_path, results, config, Thresholds())
    reviews = [json.loads(line) for line in (tmp_path / "review.jsonl").read_text().splitlines()]
    assert len(reviews) == len(review_cases(results))
    assert any("deterministic_finding" in item["reasons"] for item in reviews)
    with (tmp_path / "turns.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == sum(len(p.baseline.turns) + len(p.hardened.turns) for p in results)
    gallery = response_gallery(results)
    assert "<script>" not in gallery and "&lt;script&gt;" in gallery
    assert "Observed model responses" in gallery
    assert csv_text("=HYPERLINK(x)").startswith("'")
    assert csv_text("  @SUM(A1)").startswith("'")
    assert csv_text("invoice-id") == "invoice-id"
