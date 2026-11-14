"""Opt in using REDTEAM_LOCAL_MODEL; no downloads or model provisioning happen here."""

import os
import random

import pytest

from redteam_eval.backends import HTTPBackend
from redteam_eval.models import BackendConfig, Message, Request


@pytest.mark.local_llm
async def test_live_local_inference():
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL to opt into installed local hardware")
    config = BackendConfig(
        kind=os.getenv("REDTEAM_LOCAL_BACKEND", "ollama"),
        model=model,
        endpoint=os.getenv("REDTEAM_LOCAL_ENDPOINT", "http://127.0.0.1:11434"),
    )
    rng = random.Random(7)
    left, right = rng.randrange(10, 100), rng.randrange(10, 100)
    response = await HTTPBackend(config).generate(
        Request(
            messages=[
                Message(
                    role="user", content=f"Return the result of {left} + {right} in one sentence."
                )
            ],
            seed=7,
            max_tokens=64,
        )
    )
    assert response.text and response.usage.output_tokens > 0
    assert str(left + right) in response.text
