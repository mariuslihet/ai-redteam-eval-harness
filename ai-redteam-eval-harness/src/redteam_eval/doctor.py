"""Read-only local engine readiness checks and an optional real inference probe."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from typing import Any

import httpx
from pydantic import Field

from redteam_eval.backends import loopback_url, make_backend
from redteam_eval.models import BackendConfig, Message, Model, Request, Response


class Diagnosis(Model):
    backend: str
    model: str
    endpoint: str | None
    healthy: bool = False
    available_models: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    engine_version: str | None = None
    issues: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    probe_response: Response | None = None
    probe_expected: int | None = None


async def diagnose(
    config: BackendConfig,
    *,
    probe: bool = False,
    seed: int = 42,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Diagnosis:
    """Inspect only the configured local backend; never provision models or fetch weights.

    HTTP metadata reads cannot prove that an independently operated engine has no cloud
    proxying. The optional probe is a real generation and is clearly retained as evidence.
    """
    endpoint = None if config.kind == "transformers" else loopback_url(config.endpoint)
    result = Diagnosis(backend=config.kind, model=config.model, endpoint=endpoint)
    if config.model.endswith((":cloud", "-cloud")):
        result.issues.append("Cloud-routed models are disabled")
        return result
    if config.kind == "transformers":
        directory = Path(config.model)
        if not directory.is_dir():
            result.issues.append("Local model directory does not exist")
        else:
            if not (directory / "config.json").is_file():
                result.issues.append("Model directory is missing config.json")
            if not any(
                (directory / name).exists()
                for name in ("tokenizer.json", "tokenizer.model", "tokenizer_config.json")
            ):
                result.issues.append("Model directory has no recognized tokenizer files")
        missing = [
            name
            for name in ("transformers", "torch", "lmformatenforcer")
            if importlib.util.find_spec(name) is None
        ]
        if missing:
            result.issues.append("Missing optional dependencies: " + ", ".join(missing))
        result.details["check_scope"] = (
            "Directory and dependency checks only; use --probe to load the model"
        )
    else:
        assert endpoint is not None
        try:
            async with httpx.AsyncClient(
                timeout=min(config.timeout, 15),
                trust_env=False,
                follow_redirects=False,
                transport=transport,
            ) as client:
                if config.kind == "ollama":
                    version = await client.get(endpoint + "/api/version")
                    version.raise_for_status()
                    result.engine_version = str(version.json()["version"])
                    tags = await client.get(endpoint + "/api/tags")
                    tags.raise_for_status()
                    models = tags.json()["models"]
                    result.available_models = [str(m["name"]) for m in models]
                    selected = next((m for m in models if m["name"] == config.model), None)
                    if selected is None:
                        result.issues.append(
                            "Configured model is not installed; select an available model"
                        )
                    else:
                        result.details["model_digest"] = selected.get("digest")
                        show = await client.post(
                            endpoint + "/api/show", json={"model": config.model}
                        )
                        show.raise_for_status()
                        data = show.json()
                        result.capabilities = [str(v) for v in data.get("capabilities", [])]
                        result.details["model_details"] = data.get("details", {})
                        if result.capabilities and "completion" not in result.capabilities:
                            result.issues.append(
                                "Configured model does not support text completion"
                            )
                else:
                    path = "/models" if endpoint.endswith("/v1") else "/v1/models"
                    listing = await client.get(endpoint + path)
                    listing.raise_for_status()
                    result.available_models = [str(m["id"]) for m in listing.json()["data"]]
                    if config.model not in result.available_models:
                        result.issues.append(
                            "Configured model is not advertised by the local server"
                        )
                    result.details["check_scope"] = (
                        "Model listing only; tool/schema support requires a live evaluation"
                    )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            result.issues.append(
                f"Local engine readiness check failed: {type(exc).__name__}: {exc}"
            )
    if probe and not result.issues:
        rng = random.Random(seed)
        left, right = rng.randrange(10, 100), rng.randrange(10, 100)
        result.probe_expected = left + right
        try:
            response = await make_backend(config).generate(
                Request(
                    messages=[
                        Message(
                            role="user",
                            content=f"Calculate {left} + {right}. Return only the integer result.",
                        )
                    ],
                    seed=seed,
                    max_tokens=64,
                )
            )
            result.probe_response = response
            if response.text.strip() != str(result.probe_expected):
                result.issues.append(
                    "Live inference probe did not return the requested exact arithmetic result"
                )
        except Exception as exc:
            result.issues.append(f"Live inference probe failed: {type(exc).__name__}: {exc}")
    result.healthy = not result.issues
    return result
