"""Exercise direct pipeline loading, constraints and proposal parsing without GPU packages."""

import asyncio
import json
import sys
import time
from types import ModuleType

import pytest

from redteam_eval.backends import TransformersBackend
from redteam_eval.models import BackendConfig, Message, Request


@pytest.fixture
def fake_transformers(monkeypatch):
    observed = {"loads": [], "generations": [], "active": 0, "peak": 0}

    class Tokenizer:
        @staticmethod
        def from_pretrained(path, **kwargs):
            observed["loads"].append((path, kwargs))
            return Tokenizer()

        def apply_chat_template(self, messages, **kwargs):
            observed["messages"] = messages
            return json.dumps(messages)

        def encode(self, text):
            return list(text.encode())

    class Model:
        @staticmethod
        def from_pretrained(path, **kwargs):
            observed["loads"].append((path, kwargs))
            return object()

    class Pipeline:
        tokenizer = Tokenizer()

        def __call__(self, prompt, **kwargs):
            observed["active"] += 1
            observed["peak"] = max(observed["active"], observed["peak"])
            time.sleep(0.005)
            observed["active"] -= 1
            observed["generations"].append(kwargs)
            return [{"generated_text": observed.get("output", '{"prompt":"fixture"}')}]

    module = ModuleType("transformers")
    module.AutoTokenizer = Tokenizer
    module.AutoModelForCausalLM = Model
    module.pipeline = lambda *args, **kwargs: Pipeline()
    monkeypatch.setitem(sys.modules, "transformers", module)
    enforcer = ModuleType("lmformatenforcer")
    enforcer.JsonSchemaParser = lambda schema: schema
    integration = ModuleType("lmformatenforcer.integrations.transformers")
    integration.build_transformers_prefix_allowed_tokens_fn = lambda tokenizer, schema: "constraint"
    monkeypatch.setitem(sys.modules, "lmformatenforcer", enforcer)
    monkeypatch.setitem(sys.modules, "lmformatenforcer.integrations.transformers", integration)
    return observed


async def test_local_only_lazy_load_serialization_and_constraints(tmp_path, fake_transformers):
    backend = TransformersBackend(BackendConfig(kind="transformers", model=str(tmp_path)))
    request = Request(
        messages=[Message(role="system", content="fixture")], schema={"type": "object"}
    )
    a, b = await asyncio.gather(backend.generate(request), backend.generate(request))
    assert a.text == b.text and a.usage.output_tokens > 0
    assert fake_transformers["peak"] == 1
    assert len(fake_transformers["loads"]) == 2
    assert all(
        options == {"local_files_only": True, "trust_remote_code": False}
        for _, options in fake_transformers["loads"]
    )
    assert all(
        g["prefix_allowed_tokens_fn"] == "constraint" for g in fake_transformers["generations"]
    )
    assert not fake_transformers["generations"][0]["do_sample"]


async def test_pipeline_structured_tool_envelope(tmp_path, fake_transformers, scenario):
    fake_transformers["output"] = json.dumps(
        {
            "text": "done",
            "tool_calls": [
                {
                    "name": scenario.tools[0].name,
                    "arguments": {"invoice": scenario.metadata["invoice"]},
                }
            ],
        }
    )
    backend = TransformersBackend(BackendConfig(kind="transformers", model=str(tmp_path)))
    response = await backend.generate(
        Request(messages=[Message(role="system", content="fixture")], tools=scenario.tools)
    )
    assert response.text == "done" and len(response.tool_calls) == 1
    assert "tool_calls" in fake_transformers["messages"][0]["content"]
    fake_transformers["output"] = '{"tool_calls": [broken]}'
    response = await backend.generate(
        Request(messages=[Message(role="system", content="fixture")], tools=scenario.tools)
    )
    assert response.parse_errors
