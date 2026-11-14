import json

import pytest
from pydantic import ValidationError

from redteam_eval.archive import RunArchive, load_archive
from redteam_eval.cli import main
from redteam_eval.models import BackendConfig, Outcome, RunConfig, Thresholds
from redteam_eval.regression import RegressionLimits, compare_runs, write_regression
from redteam_eval.reporting import write_reports
from redteam_eval.runner import Runner


@pytest.fixture
async def measured(tmp_path):
    config = RunConfig(cases_per_family=1, max_turns=2)
    results = await Runner(config).run()
    write_reports(tmp_path / "original", results, config, Thresholds())
    return tmp_path / "original" / "telemetry.json"


def test_archive_round_trip(measured):
    archive = load_archive(measured)
    assert len(archive.results) == 6
    assert RunArchive.model_validate_json(archive.model_dump_json()) == archive


@pytest.mark.parametrize(
    "damage",
    [
        "missing_pair",
        "duplicate",
        "identity",
        "lineage",
        "plan",
        "mode",
        "provider",
        "model",
        "control_world",
    ],
)
def test_invalid_archives_are_rejected(measured, damage):
    data = json.loads(measured.read_text())
    pair = data["results"][0]
    if damage == "missing_pair":
        data["results"].pop()
    elif damage == "duplicate":
        data["results"][1] = pair
    elif damage == "identity":
        pair["hardened"]["scenario_id"] = "different"
    elif damage == "lineage":
        pair["baseline"]["turns"][0]["parent_id"] = "not-a-root"
    elif damage == "plan":
        pair["hardened"]["turns"][0]["prompt"] += "changed"
    elif damage == "mode":
        pair["hardened"]["mode"] = "baseline"
    elif damage == "provider":
        pair["baseline"]["provider"] = "unknown"
    elif damage == "control_world":
        pair["scenario"]["canaries"][0] += "different"
    else:
        data["target_model"] = "not-the-configured-model"
    with pytest.raises(ValidationError):
        RunArchive.model_validate(data)


def test_identical_runs_have_no_regression_even_with_confidence_gate(measured, tmp_path):
    archive = load_archive(measured)
    comparison = compare_runs(archive, archive, RegressionLimits(confidence_gate=True))
    assert not comparison["changed_cases"] and not comparison["gate_failures"]
    assert all(
        m["change"] == {"delta": 0, "lower": 0, "upper": 0} for m in comparison["metrics"].values()
    )
    write_regression(tmp_path / "comparison", comparison)
    assert {p.name for p in (tmp_path / "comparison").iterdir()} == {
        "regression.json",
        "regression.html",
        "regression.md",
    }


def test_paired_regression_reports_exact_changed_case_and_gate(measured):
    previous = load_archive(measured)
    current = previous.model_copy(deep=True)
    pair = next(p for p in current.results if not p.scenario.benign)
    verdict = pair.hardened.turns[-1].verdict
    verdict.attack_success = True
    verdict.outcome = Outcome.FULL
    verdict.findings = ["policy:regression-test"]
    comparison = compare_runs(previous, current, RegressionLimits())
    assert comparison["metrics"]["asr"]["change"]["delta"] == pytest.approx(1 / 3)
    assert comparison["metrics"]["deterministic_violation_rate"]["current"]["successes"] == 1
    assert comparison["changed_cases"][0]["key"] == pair.key
    assert len(comparison["gate_failures"]) == 2
    allowed = compare_runs(
        previous, current, RegressionLimits(max_asr_increase=0.5, max_violation_increase=0.5)
    )
    assert not allowed["gate_failures"]
    conservative = compare_runs(
        previous,
        current,
        RegressionLimits(max_asr_increase=0.5, max_violation_increase=0.5, confidence_gate=True),
    )
    assert conservative["gate_failures"]


@pytest.mark.parametrize("difference", ["scenario", "seed", "judge", "max_tokens", "keys"])
def test_unmatched_experiments_cannot_be_compared(measured, difference):
    previous = load_archive(measured)
    current = previous.model_copy(deep=True)
    if difference == "scenario":
        current.results[0].scenario.document += "different world"
    elif difference == "seed":
        current.config.seed += 1
    elif difference == "judge":
        current.config.judge = BackendConfig(model="other-judge")
    elif difference == "max_tokens":
        current.config.max_tokens += 1
    else:
        current.results[0].key = "other-key"
    with pytest.raises(ValueError, match="cannot be compared"):
        compare_runs(previous, current, RegressionLimits())


def test_target_and_defense_changes_can_be_compared(measured):
    previous = load_archive(measured)
    current = previous.model_copy(deep=True)
    current.config.backend.model = "another-local-target"
    current.target_model = "another-local-target"
    current.config.guard.input_gate = False
    assert not compare_runs(previous, current, RegressionLimits())["gate_failures"]


def test_regrade_and_compare_cli_never_calls_inference(measured, tmp_path, monkeypatch, capsys):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Archive regrading must never call model inference")

    monkeypatch.setattr(Runner, "run", forbidden)
    original_bytes = measured.read_bytes()
    output = tmp_path / "regraded"
    assert (
        main(
            ["--report-from", str(measured), "--compare-to", str(measured), "--output", str(output)]
        )
        == 0
    )
    assert measured.read_bytes() == original_bytes
    assert load_archive(output / "telemetry.json").results == load_archive(measured).results
    assert '"inference_requested": false' in capsys.readouterr().out
    assert (
        main(
            ["--report-from", str(measured), "--output", str(tmp_path / "strict"), "--max-frr", "0"]
        )
        == 1
    )
    assert main(["--report-from", str(measured), "--output", str(measured.parent)]) == 2
    assert main(["--report-from", str(measured), "--seed", "2"]) == 2
    assert main(["--report-from", str(measured), "--checkpoint", str(tmp_path / "state")]) == 2


def test_cli_regression_failure_and_input_preservation(measured, tmp_path, monkeypatch):
    archive = load_archive(measured)
    next(p for p in archive.results if not p.scenario.benign).hardened.turns[
        -1
    ].verdict.attack_success = True
    current_path = tmp_path / "current.json"
    current_path.write_text(archive.model_dump_json())
    assert (
        main(
            [
                "--report-from",
                str(current_path),
                "--compare-to",
                str(measured),
                "--output",
                str(tmp_path / "regressed"),
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "--report-from",
                str(current_path),
                "--compare-to",
                str(measured),
                "--output",
                str(measured.parent),
            ]
        )
        == 2
    )


def test_archive_regrading_preserves_saved_gates_and_recomputes_summary(measured, tmp_path):
    archive = load_archive(measured)
    archive.thresholds.max_frr = 0
    archive.summary["hardened"]["frr"]["rate"] = 0  # Stale cached summary is not evidence.
    strict = tmp_path / "strict-source.json"
    strict.write_text(archive.model_dump_json())
    assert main(["--report-from", str(strict), "--output", str(tmp_path / "preserved")]) == 1
    result = load_archive(tmp_path / "preserved" / "telemetry.json")
    assert result.thresholds.max_frr == 0
    assert result.summary["hardened"]["frr"]["rate"] == 1
    assert (
        main(
            [
                "--report-from",
                str(strict),
                "--max-frr",
                "1",
                "--output",
                str(tmp_path / "overridden"),
            ]
        )
        == 0
    )
