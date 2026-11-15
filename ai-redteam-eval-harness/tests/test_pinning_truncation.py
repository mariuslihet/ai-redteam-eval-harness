import csv
import json

import httpx
import pytest
from pydantic import ValidationError

from redteam_eval.audit import review_cases
from redteam_eval.backends import HTTPBackend
from redteam_eval.cli import build_config, parser
from redteam_eval.doctor import diagnose
from redteam_eval.models import BackendConfig, Message, Request, Response, RunConfig, Thresholds
from redteam_eval.reporting import gate, summarize, write_reports
from redteam_eval.runner import Runner


async def test_pinning_verifies_each_request_and_blocks_changed_weights():
    digest = "a" * 64
    generations = []
    metadata_calls = []

    def handle(request):
        if request.url.path == "/api/tags":
            metadata_calls.append(1)
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "local",
                            "digest": digest if len(metadata_calls) == 1 else "b" * 64,
                        }
                    ]
                },
            )
        generations.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "local",
                "done_reason": "stop",
                "message": {"content": "real transport result"},
                "prompt_eval_count": 3,
                "eval_count": 4,
            },
        )

    backend = HTTPBackend(
        BackendConfig(model="local", expected_digest=digest), httpx.MockTransport(handle)
    )
    request = Request(messages=[Message(role="user", content="fixture")])
    response = await backend.generate(request)
    assert response.model_digest == digest and response.finish_reason == "stop"
    with pytest.raises(ValueError, match="pinned model"):
        await backend.generate(request)
    assert len(generations) == 1 and len(metadata_calls) == 2


def test_pin_configuration_and_separate_judge_models():
    with pytest.raises(ValidationError):
        BackendConfig(expected_digest="short")
    with pytest.raises(ValidationError):
        BackendConfig(kind="openai", expected_digest="a" * 64)
    args = parser().parse_args(["--model-digest", "a" * 64, "--judge-model", "other-model"])
    config = build_config(args)
    assert config.backend.expected_digest == config.attacker.expected_digest == "a" * 64
    assert config.judge.expected_digest is None


async def test_readiness_rejects_pin_mismatch_without_probe():
    def handle(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "fixture"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local", "digest": "b" * 64}]})
        assert request.url.path == "/api/show"
        return httpx.Response(200, json={"capabilities": ["completion"]})

    result = await diagnose(
        BackendConfig(model="local", expected_digest="a" * 64),
        probe=True,
        transport=httpx.MockTransport(handle),
    )
    assert not result.healthy and result.probe_response is None
    assert any("pinned model" in issue for issue in result.issues)


@pytest.mark.parametrize(
    "envelope",
    [
        {"done_reason": "length"},
        {"choices": [{"finish_reason": "length"}]},
        {"choices": [{"finish_reason": "max_tokens"}]},
    ],
)
def test_native_token_limit_metadata_is_preserved_without_changing_text(envelope):
    response = Response(text="partial native response", response_envelope=envelope)
    assert response.truncated and response.finish_reason in ("length", "max_tokens")
    assert response.text == "partial native response"
    assert Response.model_validate_json(response.model_dump_json()) == response


@pytest.mark.parametrize("reason", ["stop", "tool_calls", "end_turn", None])
def test_normal_or_unknown_finish_reasons_are_not_reported_as_truncation(reason):
    response = Response(text="fixture", finish_reason=reason)
    assert not response.truncated


async def test_truncation_metrics_review_export_and_quality_gate(tmp_path):
    config = RunConfig(cases_per_family=1, max_turns=1)
    results = await Runner(config).run()
    results[0].hardened.turns[0].response = Response(text="cut off", finish_reason="length")
    summary = summarize(results, config.seed)
    assert summary["hardened"]["truncation_rate"]["successes"] == 1
    assert summary["hardened"]["truncation_rate"]["rate"] == pytest.approx(1 / 6)
    assert any("truncation_rate" in f for f in gate(summary, Thresholds(max_truncation_rate=0)))
    assert any("generation_truncated" in case.reasons for case in review_cases(results))
    write_reports(tmp_path, results, config, Thresholds())
    with (tmp_path / "turns.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert any(row["truncated"] == "True" and row["finish_reason"] == "length" for row in rows)
    assert "Generation reached its token limit" in (tmp_path / "audit.html").read_text()
