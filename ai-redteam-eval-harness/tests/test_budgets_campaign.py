import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from redteam_eval.archive import load_archive
from redteam_eval.backends import HTTPBackend
from redteam_eval.budgets import BudgetExceeded
from redteam_eval.campaign import CampaignSpec, run_campaign
from redteam_eval.cli import main
from redteam_eval.models import (
    BackendConfig,
    Message,
    Request,
    Response,
    RunConfig,
    Thresholds,
    Usage,
)
from redteam_eval.runner import Runner
from tests.doubles import DeterministicBackend


def exact_backend():
    return DeterministicBackend(
        callback=lambda request: Response(
            text="bounded completion", usage=Usage(input_tokens=2, output_tokens=3)
        )
    )


async def test_token_budget_stops_after_real_observed_usage_and_records_overage(tmp_path):
    backend = exact_backend()
    runner = Runner(
        RunConfig(cases_per_family=1, max_turns=1, concurrency=1, max_total_tokens=6),
        backend=backend,
    )
    with pytest.raises(ExceptionGroup):
        await runner.run(tmp_path)
    record = runner.budget.snapshot()
    assert record.status == "budget_exceeded"
    assert record.observed_tokens == record.total_tokens == 10
    assert record.model_responses == len(backend.requests) == 2
    assert not (tmp_path / ".run.lock").exists()
    assert not list(tmp_path.glob("pair-*.json"))


async def test_budget_counts_judge_and_attacker_usage():
    config = RunConfig(cases_per_family=1, max_turns=2, concurrency=1, max_total_tokens=1000)
    attacker = DeterministicBackend(
        callback=lambda request: Response(
            text='{"prompt":"refined followup"}', usage=Usage(input_tokens=7, output_tokens=11)
        )
    )
    judge = DeterministicBackend(
        callback=lambda request: Response(
            text=json.dumps(
                {
                    "outcome": "FALSE_REFUSAL_ON_BENIGN"
                    if '"benign": true' in request.messages[1].content
                    else "SAFE_REFUSAL",
                    "confidence": 0.9,
                    "rationale": "Test fixture evaluation",
                    "evidence": "",
                }
            ),
            usage=Usage(input_tokens=13, output_tokens=17),
        )
    )
    runner = Runner(config, backend=exact_backend(), attacker=attacker, judge=judge)
    results = await runner.run()
    expected = sum(
        turn.response.usage.input_tokens
        + turn.response.usage.output_tokens
        + turn.attacker_usage.input_tokens
        + turn.attacker_usage.output_tokens
        + turn.judge_usage.input_tokens
        + turn.judge_usage.output_tokens
        for pair in results
        for trace in (pair.baseline, pair.hardened)
        for turn in trace.turns
    )
    assert runner.budget.snapshot().observed_tokens == expected
    assert runner.budget.snapshot().status == "complete"


async def test_estimated_usage_fails_closed_only_when_token_cap_is_configured():
    backend = DeterministicBackend(
        script=[
            Response(text="estimated", usage=Usage(input_tokens=3, output_tokens=2, estimated=True))
        ]
    )
    runner = Runner(
        RunConfig(cases_per_family=1, max_turns=1, max_total_tokens=100), backend=backend
    )
    with pytest.raises(ExceptionGroup):
        await runner.run()
    assert runner.budget.snapshot().estimated_responses >= 1
    assert runner.budget.snapshot().status == "budget_exceeded"


async def test_deadline_cancels_workers_and_releases_checkpoint_lock(tmp_path):
    stopped = asyncio.Event()

    class SlowBackend:
        async def generate(self, request):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    runner = Runner(
        RunConfig(cases_per_family=1, concurrency=1, max_run_seconds=0.02), backend=SlowBackend()
    )
    with pytest.raises(BudgetExceeded, match="time allowance"):
        await runner.run(tmp_path)
    assert stopped.is_set()
    assert runner.budget.snapshot().status == "budget_exceeded"
    assert not (tmp_path / ".run.lock").exists()


async def test_resume_restores_usage_before_calls_and_allows_raising_limits(tmp_path):
    config = RunConfig(cases_per_family=1, max_turns=1, concurrency=1, max_total_tokens=100)
    backend = exact_backend()
    runner = Runner(config, backend=backend)
    results = await runner.run(tmp_path)
    assert runner.budget.snapshot().observed_tokens == 60
    calls = len(backend.requests)
    lowered = config.model_copy(update={"max_total_tokens": 59})
    low_runner = Runner(lowered, backend=backend)
    with pytest.raises(BudgetExceeded):
        await low_runner.run(tmp_path)
    assert len(backend.requests) == calls
    raised = config.model_copy(update={"max_total_tokens": 120, "max_run_seconds": 2})
    restored_runner = Runner(raised, backend=backend)
    assert await restored_runner.run(tmp_path) == results
    state = restored_runner.budget.snapshot()
    assert state.restored_tokens == state.total_tokens == 60
    assert state.observed_tokens == state.model_responses == 0 and state.restored_pairs == 6
    frozen = state.elapsed_seconds
    await asyncio.sleep(0)
    assert restored_runner.budget.snapshot().elapsed_seconds == frozen


@pytest.mark.parametrize("seeds", [[1], [1, 1], [-1, 2], [1, 2**63]])
def test_campaign_seed_validation(seeds):
    with pytest.raises(ValidationError):
        CampaignSpec(seeds=seeds)


async def test_campaign_reports_both_seeds_and_resumes_without_calls(tmp_path, monkeypatch):
    backend = exact_backend()
    monkeypatch.setattr("redteam_eval.runner.make_backend", lambda config: backend)
    config = RunConfig(cases_per_family=1, max_turns=1, max_total_tokens=100)
    spec = CampaignSpec(seeds=[10, 11])
    output, checkpoint = tmp_path / "reports", tmp_path / "state"
    record = await run_campaign(config, spec, Thresholds(), output, checkpoint)
    assert record.status == "complete" and len(record.seed_results) == 2
    assert record.summary["pairs"] == 12
    assert record.summary["hardened"]["asr"]["trials"] == 6
    assert not record.gate_failures
    for seed in spec.seeds:
        archive = load_archive(output / f"seed-{seed}" / "telemetry.json")
        assert archive.config.seed == seed
        assert (output / f"seed-{seed}" / "execution.json").exists()
    calls = len(backend.requests)
    replayed = await run_campaign(config, spec, Thresholds(), output, checkpoint)
    assert len(backend.requests) == calls
    assert all(
        item.execution.model_responses == 0 and item.execution.restored_pairs == 6
        for item in replayed.seed_results
    )
    assert not (output / ".campaign.lock").exists()


async def test_campaign_cannot_hide_a_failing_seed_with_a_pooled_average(tmp_path, monkeypatch):
    from redteam_eval.models import Family, ToolCall
    from redteam_eval.synthesis import synthesize

    def factory(config):
        scenarios = synthesize(config.seed, config.cases_per_family)

        def respond(request):
            if not any("Security review" in m.content for m in request.messages):
                return Response(text="safe")
            control = next(
                s for s in scenarios if s.benign and s.prompt == request.messages[-1].content
            )
            if config.seed == 1 and control.family == Family.INJECTION:
                return Response(text="refused")
            calls = []
            if control.expected_tools:
                calls = [
                    ToolCall(
                        name=control.expected_tools[0],
                        arguments={"invoice": control.metadata["invoice"]},
                    ),
                    ToolCall(
                        name=control.expected_tools[1],
                        arguments={
                            "invoice": control.metadata["invoice"],
                            "amount": float(control.metadata["amount"]),
                        },
                    ),
                ]
            return Response(text=control.expected_text, tool_calls=calls)

        return DeterministicBackend(callback=respond)

    monkeypatch.setattr(
        "redteam_eval.campaign.Runner", lambda config: Runner(config, backend=factory(config))
    )
    record = await run_campaign(
        RunConfig(cases_per_family=1, max_turns=1),
        CampaignSpec(seeds=[1, 2]),
        Thresholds(max_frr=0.2),
        tmp_path,
    )
    assert record.status == "complete" and record.gate_failures
    assert record.summary["hardened"]["frr"]["rate"] == pytest.approx(1 / 6)
    assert not any(f.startswith("pooled:") for f in record.gate_failures)
    assert any(f.startswith("seed 1:") for f in record.gate_failures)
    assert not any(f.startswith("seed 2:") for f in record.gate_failures)


async def test_failed_campaign_has_no_aggregate_and_locks_are_released(tmp_path, monkeypatch):
    class BrokenBackend:
        async def generate(self, request):
            raise ValueError("backend failed")

    monkeypatch.setattr("redteam_eval.runner.make_backend", lambda config: BrokenBackend())
    with pytest.raises(ExceptionGroup):
        await run_campaign(
            RunConfig(cases_per_family=1), CampaignSpec(seeds=[1, 2]), Thresholds(), tmp_path
        )
    data = json.loads((tmp_path / "campaign.json").read_text())
    assert data["status"] == "failed" and data["summary"] is None
    assert "incomplete" in (tmp_path / "campaign.html").read_text()
    assert not (tmp_path / ".campaign.lock").exists()


def test_cli_execution_log_on_budget_failure_and_campaign_validation(tmp_path):
    output = tmp_path / "budget"
    assert (
        main(["--cases-per-family", "1", "--max-total-tokens", "1", "--output", str(output)]) == 2
    )
    assert json.loads((output / "execution.json").read_text())["status"] == "budget_exceeded"
    assert not (output / "telemetry.json").exists()
    assert main(["--seeds", "1", "1", "--output", str(tmp_path / "duplicate")]) == 2
    assert main(["--seeds", "1", "2", "--seed", "3"]) == 2
    assert main(["--doctor", "--max-total-tokens", "5"]) == 2


@pytest.mark.parametrize("kind", ["ollama", "openai"])
async def test_partial_native_usage_cannot_claim_exact_token_accounting(kind):
    def handle(request):
        if kind == "ollama":
            return httpx.Response(
                200, json={"message": {"content": "observed output"}, "prompt_eval_count": 3}
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "observed output"}}],
                "usage": {"prompt_tokens": 3},
            },
        )

    response = await HTTPBackend(BackendConfig(kind=kind), httpx.MockTransport(handle)).generate(
        Request(messages=[Message(role="user", content="fixture")])
    )
    assert response.usage.estimated and response.usage.output_tokens == 0


async def test_campaign_recipe_mismatch_preserves_previous_reports(tmp_path):
    config = RunConfig(cases_per_family=1, max_turns=1)
    await run_campaign(config, CampaignSpec(seeds=[1, 2]), Thresholds(), tmp_path)
    before = (tmp_path / "campaign.json").read_bytes()
    with pytest.raises(ValueError, match="different model/configuration/seed recipe"):
        await run_campaign(config, CampaignSpec(seeds=[3, 4]), Thresholds(), tmp_path)
    assert (tmp_path / "campaign.json").read_bytes() == before
    assert not (tmp_path / ".campaign.lock").exists()


async def test_campaign_output_has_only_one_owner_and_cancel_cleans_up(tmp_path, monkeypatch):
    started = asyncio.Event()

    class WaitingBackend:
        async def generate(self, request):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr("redteam_eval.runner.make_backend", lambda config: WaitingBackend())
    config = RunConfig(cases_per_family=1)
    owner = asyncio.create_task(
        run_campaign(config, CampaignSpec(seeds=[1, 2]), Thresholds(), tmp_path)
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    with pytest.raises(ValueError, match="locked"):
        await run_campaign(config, CampaignSpec(seeds=[1, 2]), Thresholds(), tmp_path)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert not (tmp_path / ".campaign.lock").exists()
    assert json.loads((tmp_path / "campaign.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1])
def test_time_allowance_must_be_positive_and_finite(value):
    with pytest.raises(ValidationError):
        RunConfig(max_run_seconds=value)
