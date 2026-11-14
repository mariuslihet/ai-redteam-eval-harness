import json

import httpx
import pytest

from redteam_eval.backends import HTTPBackend, TransformersBackend, loopback_url
from redteam_eval.models import (
    BackendConfig,
    Message,
    ProviderProfile,
    Request,
    Response,
    ToolCall,
    Usage,
)
from redteam_eval.providers import ProviderCodec, ProviderSimulator, RateLimited


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://10.0.0.1:80",
        "http://model.internal",
        "http://127.0.0.1.evil",
        "http://user@127.0.0.1",
        "http://[::]",
        "http://127.0.0.1?a=b",
        "file:///tmp/socket",
    ],
)
def test_reject_nonlocal_or_ambiguous_endpoints(url):
    with pytest.raises(ValueError):
        loopback_url(url)


def test_localhost_normalization_and_model_download_rejection(tmp_path):
    assert loopback_url("http://localhost:11434/") == "http://127.0.0.1:11434"
    assert loopback_url("http://[::1]:8000/v1") == "http://[::1]:8000/v1"
    with pytest.raises(ValueError):
        HTTPBackend(BackendConfig(model="x:cloud"))
    with pytest.raises(ValueError):
        TransformersBackend(BackendConfig(kind="transformers", model=str(tmp_path / "absent")))


@pytest.mark.parametrize("kind", ["ollama", "openai"])
async def test_real_adapter_wire_format_via_mock_transport(kind, scenario):
    async def handler(request):
        data = json.loads(request.content)
        assert data["tools"][0]["function"]["name"] == scenario.tools[0].name
        assert not data["stream"]
        if kind == "ollama":
            assert request.url.path == "/api/chat"
            assert data["format"] == {"type": "object"}
            assert data["options"]["seed"] == 17
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": "ok",
                        "tool_calls": [{"function": {"name": "f", "arguments": {"x": 1}}}],
                    },
                    "prompt_eval_count": 9,
                    "eval_count": 4,
                },
            )
        assert request.url.path == "/v1/chat/completions"
        assert data["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"function": {"name": "f", "arguments": '{"x": 1}'}}],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 4},
            },
        )

    backend = HTTPBackend(
        BackendConfig(kind=kind, model="local", endpoint="http://localhost:8080"),
        transport=httpx.MockTransport(handler),
    )
    response = await backend.generate(
        Request(
            messages=[Message(role="user", content="test")],
            tools=scenario.tools,
            seed=17,
            schema={"type": "object"},
        )
    )
    assert response.tool_calls[0].arguments == {"x": 1}
    assert response.usage == Usage(input_tokens=9, output_tokens=4)


async def test_redirect_does_not_escape_loopback():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://example.com"})

    backend = HTTPBackend(BackendConfig(), httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await backend.generate(Request(messages=[Message(role="user", content="test")]))
    assert len(seen) == 1


@pytest.mark.parametrize("name", ["openai", "anthropic", "azure", "bedrock"])
def test_full_provider_request_response_round_trip(name, scenario):
    codec = ProviderCodec(ProviderProfile(name=name))
    request = Request(
        messages=[
            Message(role="system", content=scenario.system),
            Message(role="user", content="business record"),
        ],
        tools=scenario.tools,
        seed=4,
        schema={"type": "object"},
    )
    encoded = codec.encode_request(request)
    assert codec.decode_request(encoded, request) == request
    response = Response(
        text="café\npublic",
        tool_calls=[
            ToolCall(id="a", name=scenario.tools[0].name, arguments={"invoice": "INV-123456"})
        ],
        usage=Usage(input_tokens=11, output_tokens=5),
    )
    wire = codec.encode_response(response, 4)
    decoded = codec.decode_response(wire, response)
    assert decoded.text == response.text
    assert decoded.tool_calls == response.tool_calls
    assert decoded.usage == response.usage


async def test_rate_limit_does_not_call_backend():
    from tests.doubles import DeterministicBackend

    backend = DeterministicBackend()
    profile = ProviderProfile(name="bedrock", rate_limit_probability=0.999999)
    simulator = ProviderSimulator(backend, profile)
    with pytest.raises(RateLimited):
        await simulator.generate(Request(messages=[], seed=42))
    assert not backend.requests
