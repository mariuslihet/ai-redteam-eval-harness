import json

import httpx
import pytest

from redteam_eval.cli import main
from redteam_eval.doctor import Diagnosis, diagnose
from redteam_eval.models import BackendConfig, Response, Usage
from tests.doubles import DeterministicBackend


def ollama_transport(*, installed=True, completion=True):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "fixture-version"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [{"name": "local-model", "digest": "local-digest"}]
                    if installed
                    else []
                },
            )
        if request.url.path == "/api/show":
            assert json.loads(request.content) == {"model": "local-model"}
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "tools"] if completion else ["embedding"],
                    "details": {"format": "gguf"},
                },
            )
        raise AssertionError(f"Readiness check attempted unexpected endpoint {request.url}")

    return httpx.MockTransport(handle), requests


async def test_ollama_metadata_checks_never_generate_or_download():
    transport, requests = ollama_transport()
    result = await diagnose(BackendConfig(model="local-model"), transport=transport)
    assert result.healthy and result.engine_version == "fixture-version"
    assert result.available_models == ["local-model"]
    assert result.details["model_digest"] == "local-digest"
    assert result.probe_response is None
    assert [r.url.path for r in requests] == ["/api/version", "/api/tags", "/api/show"]


@pytest.mark.parametrize("installed,completion", [(False, True), (True, False)])
async def test_missing_or_embedding_only_model_is_unhealthy(installed, completion):
    transport, _ = ollama_transport(installed=installed, completion=completion)
    result = await diagnose(BackendConfig(model="local-model"), transport=transport)
    assert not result.healthy and result.issues


@pytest.mark.parametrize(
    "endpoint,path",
    [("http://127.0.0.1:8080", "/v1/models"), ("http://127.0.0.1:8080/v1", "/v1/models")],
)
async def test_openai_compatible_local_model_listing(endpoint, path):
    def handle(request):
        assert request.url.path == path
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    result = await diagnose(
        BackendConfig(kind="openai", model="local-model", endpoint=endpoint),
        transport=httpx.MockTransport(handle),
    )
    assert result.healthy and result.available_models == ["local-model"]


async def test_unavailable_malformed_and_redirected_engines_are_visible():
    def unavailable(request):
        raise httpx.ConnectError("local server unavailable")

    def malformed(request):
        return httpx.Response(200, json={"unexpected": True})

    def redirect(request):
        return httpx.Response(307, headers={"location": "https://example.com"})

    for handler in (unavailable, malformed, redirect):
        result = await diagnose(BackendConfig(), transport=httpx.MockTransport(handler))
        assert not result.healthy and result.issues
        assert result.probe_response is None


async def test_no_remote_endpoint_or_cloud_model_allowed():
    with pytest.raises(ValueError, match="loopback"):
        await diagnose(BackendConfig(endpoint="http://model.example"))
    result = await diagnose(BackendConfig(model="model:cloud"))
    assert not result.healthy and "Cloud-routed" in result.issues[0]


async def test_probe_uses_backend_response_and_validates_answer(monkeypatch):
    import random

    rng = random.Random(42)
    expected = str(rng.randrange(10, 100) + rng.randrange(10, 100))
    transport, _ = ollama_transport()
    backend = DeterministicBackend(
        script=[Response(text=expected, usage=Usage(input_tokens=4, output_tokens=2))]
    )
    monkeypatch.setattr("redteam_eval.doctor.make_backend", lambda config: backend)
    result = await diagnose(BackendConfig(model="local-model"), probe=True, transport=transport)
    assert result.healthy and result.probe_response.text == expected
    assert len(backend.requests) == 1
    bad = DeterministicBackend(script=[Response(text="wrong answer")])
    monkeypatch.setattr("redteam_eval.doctor.make_backend", lambda config: bad)
    result = await diagnose(BackendConfig(model="local-model"), probe=True, transport=transport)
    assert not result.healthy and result.probe_response.text == "wrong answer"


async def test_transformers_readiness_does_not_import_or_download_weights(tmp_path, monkeypatch):
    monkeypatch.setattr("redteam_eval.doctor.importlib.util.find_spec", lambda module: None)
    result = await diagnose(BackendConfig(kind="transformers", model=str(tmp_path)))
    assert not result.healthy and len(result.issues) == 3
    for name in ("config.json", "tokenizer.json"):
        (tmp_path / name).write_text("{}")
    monkeypatch.setattr("redteam_eval.doctor.importlib.util.find_spec", lambda module: object())
    result = await diagnose(BackendConfig(kind="transformers", model=str(tmp_path)))
    assert result.healthy and result.endpoint is None and result.probe_response is None


def test_doctor_cli_status_and_argument_validation(tmp_path, monkeypatch):
    async def healthy(*args, **kwargs):
        return Diagnosis(
            backend="ollama", model="local-model", endpoint="http://127.0.0.1:11434", healthy=True
        )

    monkeypatch.setattr("redteam_eval.cli.diagnose", healthy)
    assert main(["--doctor", "--output", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "doctor.json").read_text())["healthy"] is True

    async def unhealthy(*args, **kwargs):
        return Diagnosis(
            backend="ollama", model="local-model", endpoint=None, issues=["not installed"]
        )

    monkeypatch.setattr("redteam_eval.cli.diagnose", unhealthy)
    assert main(["--doctor", "--output", str(tmp_path)]) == 1
    assert main(["--probe"]) == 2
    assert main(["--doctor", "--compare-to", "other.json"]) == 2
    assert main(["--doctor", "--checkpoint", str(tmp_path)]) == 2
