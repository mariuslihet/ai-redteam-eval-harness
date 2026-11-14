"""Programmable model fixture exclusively for automated tests."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from redteam_eval.models import Request, Response, Usage


class DeterministicBackend:
    """Test-only vulnerable model; programmable by callback or scripted turn responses.

    This is never represented as a real LLM or as evidence about model security.
    """

    def __init__(
        self,
        script: list[Response] | None = None,
        callback: Callable[[Request], Response] | None = None,
    ):
        self.script = script
        self.callback = callback
        self.requests: list[Request] = []

    async def generate(self, request: Request) -> Response:
        self.requests.append(request.model_copy(deep=True))
        if self.callback:
            return self.callback(request)
        depth = sum(m.role == "assistant" for m in request.messages)
        if self.script:
            return self.script[min(depth, len(self.script) - 1)].model_copy(deep=True)
        system = request.messages[0].content
        if request.schema_:
            # Judge schema and attacker schema are deliberately distinct.
            if "outcome" in request.schema_.get("properties", {}):
                text = json.dumps(
                    {
                        "outcome": "SAFE_REFUSAL",
                        "confidence": 0.7,
                        "rationale": "Deterministic judge test fixture",
                    }
                )
            else:
                text = json.dumps({"prompt": request.messages[-1].content[-200:]})
        else:
            vault = re.search(r"sk_synthetic_[A-Za-z0-9]+", system)
            text = vault.group() if vault else "No fixture secret present."
            if depth == 0:
                text = "I cannot disclose private credentials."
        return Response(
            text=text,
            usage=Usage(
                input_tokens=sum(len(m.content) for m in request.messages) // 4,
                output_tokens=max(1, len(text) // 4),
                estimated=True,
            ),
        )
