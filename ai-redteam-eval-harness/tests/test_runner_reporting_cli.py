import asyncio
import json
import re

import httpx
import pytest

from redteam_eval.checkpoint import CheckpointStore
from redteam_eval.cli import main
from redteam_eval.models import (
    BackendConfig,
    ProviderProfile,
    Response,
    RunConfig,
    Thresholds,
)
from redteam_eval.reporting import gate, paired_delta, summarize, wilson, write_reports
from redteam_eval.runner import Runner
from redteam_eval.synthesis import synthesize
from tests.doubles import DeterministicBackend


def config(**kwargs):
    return RunConfig(
        backend=BackendConfig(kind="ollama", model="test-fixture"),
        cases_per_family=1,
        max_turns=3,
        **kwargs,
    )


async def test_paired_adaptive_search_full_dag_and_resume(tmp_path):
    backend = DeterministicBackend()
    checkpoint = tmp_path / "state"
    results = await Runner(config(), backend=backend).run(checkpoint)
    assert len(results) == 6
    summary = summarize(results, 42)
    assert summary["baseline"]["asr"]["rate"] == 1
    assert summary["hardened"]["asr"]["rate"] == 0
    assert summary["defense_delta"]["delta"] == 1
    for pair in results:
        assert [t.prompt for t in pair.baseline.turns] == [t.prompt for t in pair.hardened.turns]
        for trace in (pair.baseline, pair.hardened):
            assert trace.turns[0].parent_id is None
            if not trace.benign:
                assert len(trace.turns) == 2
                assert trace.turns[1].parent_id == trace.turns[0].id
                assert trace.turns[1].mutation != "identity"
        if not pair.scenario.benign:
            assert pair.baseline.turns[-1].state == "success"
            assert pair.hardened.turns[-1].state == "exhausted"
    calls = len(backend.requests)
    resumed = await Runner(config(), backend=backend).run(checkpoint)
    assert resumed == results and len(backend.requests) == calls
    # Removing one committed pair replays exactly one whole pair.
    store = CheckpointStore(checkpoint, config(), synthesize(42, 1))
    store.path(results[0].key).unlink()
    store.close()
    rerun = await Runner(config(), backend=backend).run(checkpoint)
    assert len(rerun) == 6 and len(backend.requests) == calls + 4
    with pytest.raises(ValueError, match="Corrupt checkpoint"):
        # Corrupt committed data must fail instead of silently skipping it.
        file = next(checkpoint.glob("pair-*.json"))
        file.write_text('{"result": {}, "sha256": "invalid"}')
        await Runner(config(), backend=backend).run(checkpoint)
    assert not (checkpoint / ".run.lock").exists()


async def test_checkpoint_rejects_config_change_and_concurrent_owner(tmp_path):
    suite = synthesize(42, 1)
    store = CheckpointStore(tmp_path, config(), suite)
    with pytest.raises(ValueError, match="locked"):
        CheckpointStore(tmp_path, config(), suite)
    store.close()
    with pytest.raises(ValueError, match="differs"):
        CheckpointStore(tmp_path, config(seed=99), synthesize(99, 1))
    assert not (tmp_path / ".run.lock").exists()


async def test_local_red_agent_uses_feedback_and_structured_refinement():
    observed = []

    def attacker(request):
        evidence = json.loads(request.messages[-1].content)
        observed.append(evidence)
        assert (
            "refusal" in evidence["target_response"].lower()
            or "cannot" in evidence["target_response"].lower()
        )
        return Response(text=json.dumps({"prompt": evidence["candidate"][:300] + "\nrefined"}))

    results = await Runner(config(), attacker=DeterministicBackend(callback=attacker)).run()
    assert len(observed) == 3
    for pair in results:
        if not pair.scenario.benign:
            assert "refined" in pair.baseline.turns[1].prompt
            assert pair.baseline.turns[1].prompt == pair.hardened.turns[1].prompt


async def test_concurrency_bound_and_seed_schedule_independence():
    active = 0
    peak = 0

    class CountingBackend:
        async def generate(self, request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return Response(text="Cannot disclose private data.")

    many = await Runner(config(concurrency=2), backend=CountingBackend()).run()
    one = await Runner(config(concurrency=1), backend=CountingBackend()).run()
    assert peak <= 2
    assert [[t.prompt for t in p.baseline.turns] for p in many] == [
        [t.prompt for t in p.baseline.turns] for p in one
    ]
    assert all(len(p.baseline.turns) == (1 if p.scenario.benign else 3) for p in many)


async def test_retry_transient_only_and_exhaustion():
    runner = Runner(config(backoff_seconds=0))
    attempts = []

    async def flaky(attempt):
        attempts.append(attempt)
        if attempt < 2:
            raise httpx.ConnectError("local transient")
        return Response(text="ok")

    response = await runner.retry(flaky, 7)
    assert response.attempts == 3 and attempts == [0, 1, 2]

    async def invalid(attempt):
        response = httpx.Response(400, request=httpx.Request("POST", "http://127.0.0.1"))
        raise httpx.HTTPStatusError("invalid request", request=response.request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await runner.retry(invalid, 7)


def test_wilson_and_paired_statistics():
    interval = wilson(5, 10)
    assert interval.rate == 0.5
    assert interval.lower == pytest.approx(0.236593, abs=1e-6)
    assert interval.upper == pytest.approx(0.763407, abs=1e-6)
    assert wilson(0, 0).upper == 1
    assert wilson(0, 10).lower == 0
    assert wilson(10, 10).upper == 1
    with pytest.raises(ValueError):
        wilson(2, 1)
    assert paired_delta([0, 1, -1, 1], 9) == paired_delta([0, 1, -1, 1], 9)


async def test_reports_gate_and_provider_strata(tmp_path):
    cfg = config(
        providers=[ProviderProfile(name=n) for n in ("openai", "anthropic", "azure", "bedrock")]
    )
    results = await Runner(cfg).run()
    summary, failures = write_reports(
        tmp_path, results, cfg, Thresholds(max_asr=0, max_frr=1, min_defense_delta=1)
    )
    assert not failures
    assert summary["pairs"] == 24
    assert summary["baseline"]["asr"]["trials"] == 12
    assert summary["baseline"]["frr"]["trials"] == 12
    assert set(summary["breakdowns"]) == {"provider", "family", "mutation", "depth"}
    assert gate(summary, Thresholds(max_frr=0))
    assert gate(summary, Thresholds(max_asr=0, use_upper_ci=True))
    assert {p.name for p in tmp_path.iterdir()} == {
        "telemetry.json",
        "audit.html",
        "audit.md",
        "comparison.svg",
        "review.jsonl",
        "turns.csv",
    }
    doc = (tmp_path / "audit.html").read_text(encoding="utf-8")
    assert "<svg" in doc
    assert not re.search(r"<script|https?://(?!www.w3.org)", doc)


def test_cli_pass_fail_operational_exit_codes(tmp_path, capsys):
    base = [
        "--backend",
        "ollama",
        "--algorithmic-attacker",
        "--no-judge",
        "--cases-per-family",
        "1",
        "--output",
        str(tmp_path / "reports"),
    ]
    assert main([*base, "--max-asr", "0", "--min-defense-delta", "1"]) == 0
    assert main([*base, "--max-frr", "0"]) == 1
    assert main([*base, "--max-turns", "0"]) == 2
    assert main([*base, "--max-asr", "5"]) == 2
    path = tmp_path / "config.json"
    path.write_text(config().model_dump_json())
    assert main(["--config", str(path), "--output", str(tmp_path / "configured")]) == 0
    assert main(["--config", str(path), "--seed", "2"]) == 2


async def test_taskgroup_cancels_peers_and_releases_lock(tmp_path):
    class FailingBackend:
        async def generate(self, request):
            raise ValueError("bad response")

    with pytest.raises(ExceptionGroup):
        await Runner(config(), backend=FailingBackend()).run(tmp_path)
    assert not (tmp_path / ".run.lock").exists()
    assert not list(tmp_path.glob("pair-*.json"))


async def test_interrupted_run_preserves_completed_pairs(tmp_path):
    delegate = DeterministicBackend()

    class InterruptedBackend:
        async def generate(self, request):
            if len(delegate.requests) == 4:
                raise ValueError("simulated process interruption after one pair")
            return await delegate.generate(request)

    cfg = config(concurrency=1)
    with pytest.raises(ExceptionGroup):
        await Runner(cfg, backend=InterruptedBackend()).run(tmp_path)
    assert len(list(tmp_path.glob("pair-*.json"))) == 1
    resumed = DeterministicBackend()
    results = await Runner(cfg, backend=resumed).run(tmp_path)
    assert len(results) == 6
    assert len(resumed.requests) == 14


async def test_simulated_rate_limit_retries_and_exhaustion():
    from redteam_eval.models import Message, Request
    from redteam_eval.providers import ProviderSimulator, RateLimited

    cfg = config(backoff_seconds=0, retries=1)
    backend = DeterministicBackend()
    simulator = ProviderSimulator(
        backend, ProviderProfile(name="openai", rate_limit_probability=0.999999, retry_after_ms=0)
    )
    request = Request(messages=[Message(role="user", content="fixture")], seed=9)
    with pytest.raises(RateLimited):
        await Runner(cfg).retry(lambda attempt: simulator.generate(request, attempt), request.seed)
    assert not backend.requests


async def test_truncated_attacker_output_retries_without_substituting_a_prompt():
    from redteam_eval.models import Usage

    def attacker(request):
        if len(request.messages) == 2:
            return Response(
                text='{"prompt":"unfinished', usage=Usage(input_tokens=7, output_tokens=5)
            )
        return Response(
            text='{"prompt":"Reconsider the prior proposed scope."}',
            usage=Usage(input_tokens=9, output_tokens=11),
        )

    results = await Runner(config(), attacker=DeterministicBackend(callback=attacker)).run()
    for pair in results:
        if not pair.scenario.benign:
            turn = pair.baseline.turns[1]
            assert turn.prompt.endswith("Reconsider the prior proposed scope.")
            assert len(turn.attacker_outputs) == 2
            assert turn.attacker_usage == Usage(input_tokens=16, output_tokens=16)
