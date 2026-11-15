"""Local-only asynchronous inference adapters. All application completions use real models."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from redteam_eval.models import Backend, BackendConfig, Request, Response, ToolCall, Usage
from redteam_eval.policy import parse_calls


def loopback_url(url: str) -> str:
    """Reject DNS names, credentials and redirects; normalize localhost without DNS lookup."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme != "http" or not host or parsed.username or parsed.password:
        raise ValueError("Local endpoints must be plain HTTP loopback URLs without credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Endpoint cannot contain a query or fragment")
    host = "127.0.0.1" if host == "localhost" else host
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError("Only loopback IP endpoints are permitted")
    except ValueError as exc:
        raise ValueError("Only literal loopback IPs or localhost are permitted") from exc
    netloc = f"[{host}]" if ":" in host else host
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunsplit(("http", netloc, parsed.path.rstrip("/"), "", ""))


def tool_schema(request: Request) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in request.tools
    ]


class HTTPBackend:
    """One bounded HTTP request per inference; retries are owned by the experiment runner."""

    def __init__(self, config: BackendConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.url = loopback_url(config.endpoint)
        self.transport = transport
        if config.model.endswith((":cloud", "-cloud")):
            raise ValueError("Cloud-routed model names are disabled")

    async def generate(self, request: Request) -> Response:
        messages = [m.model_dump() for m in request.messages]
        body: dict[str, Any] = {"model": self.config.model, "messages": messages, "stream": False}
        if request.tools:
            body["tools"] = tool_schema(request)
        if self.config.kind == "ollama":
            path = "/api/chat"
            body["options"] = {
                "seed": request.seed,
                "temperature": 0,
                "num_predict": request.max_tokens,
            }
            if request.schema_:
                body["format"] = request.schema_
        else:
            path = "/chat/completions" if self.url.endswith("/v1") else "/v1/chat/completions"
            body.update(seed=request.seed, temperature=0, max_tokens=request.max_tokens)
            if request.schema_:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "evaluation",
                        "strict": True,
                        "schema": request.schema_,
                    },
                }
        start = time.perf_counter()
        async with httpx.AsyncClient(
            timeout=self.config.timeout,
            trust_env=False,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            verified_digest = None
            if self.config.expected_digest:
                tags = await client.get(self.url + "/api/tags")
                tags.raise_for_status()
                selected = next(
                    (m for m in tags.json()["models"] if m.get("name") == self.config.model), None
                )
                verified_digest = selected.get("digest") if selected else None
                if verified_digest != self.config.expected_digest:
                    raise ValueError(
                        "Installed model digest differs from the pinned model; inference was not requested"
                    )
            result = await client.post(self.url + path, json=body)
            result.raise_for_status()
            data = result.json()
        if self.config.kind == "ollama":
            message = data["message"]
            usage = Usage(
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
                estimated=any(
                    type(data.get(key)) is not int for key in ("prompt_eval_count", "eval_count")
                ),
            )
        else:
            message = data["choices"][0]["message"]
            counts = data.get("usage", {})
            usage = Usage(
                input_tokens=counts.get("prompt_tokens", 0),
                output_tokens=counts.get("completion_tokens", 0),
                estimated=any(
                    type(counts.get(key)) is not int
                    for key in ("prompt_tokens", "completion_tokens")
                ),
            )
        calls, errors = parse_calls(message.get("tool_calls", []))
        text = message.get("content") or ""
        if not isinstance(text, str):
            raise ValueError("Backend content must be a string")
        return Response(
            text=text,
            tool_calls=calls,
            parse_errors=errors,
            usage=usage,
            latency_ms=(time.perf_counter() - start) * 1000,
            request_envelope={
                "backend": self.config.kind,
                "endpoint": self.url + path,
                "body": body,
            },
            response_envelope=data,
            model_digest=verified_digest,
        )


class TransformersBackend:
    """Lazy local-directory-only transformers pipeline with serialized threaded generation.

    JSON-schema requests use lm-format-enforcer token constraints; no silent unconstrained
    fallback is allowed for the judge. Remote code and model downloads are disabled.
    """

    def __init__(self, config: BackendConfig):
        self.config = config
        if not Path(config.model).is_dir():
            raise ValueError("Transformers model must be an existing local model directory")
        self._pipeline: Any = None
        self._lock = asyncio.Lock()

    def _generate(self, request: Request) -> Response:
        from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

        if self._pipeline is None:
            tokenizer = AutoTokenizer.from_pretrained(
                self.config.model, local_files_only=True, trust_remote_code=False
            )
            model = AutoModelForCausalLM.from_pretrained(
                self.config.model, local_files_only=True, trust_remote_code=False
            )
            self._pipeline = pipeline("text-generation", model=model, tokenizer=tokenizer)
        tokenizer = self._pipeline.tokenizer
        messages = [m.model_dump() for m in request.messages]
        if request.tools:
            messages[0]["content"] += (
                '\nFor proposed tools emit JSON {"text":"","tool_calls":['
                '{"name":"function","arguments":{}}]}. Tools: ' + json.dumps(tool_schema(request))
            )
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        kwargs: dict[str, Any] = {
            "max_new_tokens": request.max_tokens,
            "do_sample": False,
            "return_full_text": False,
        }
        if request.schema_:
            from lmformatenforcer import JsonSchemaParser
            from lmformatenforcer.integrations.transformers import (
                build_transformers_prefix_allowed_tokens_fn,
            )

            kwargs["prefix_allowed_tokens_fn"] = build_transformers_prefix_allowed_tokens_fn(
                tokenizer, JsonSchemaParser(request.schema_)
            )
        start = time.perf_counter()
        result = self._pipeline(prompt, **kwargs)
        text = str(result[0]["generated_text"])
        calls: list[ToolCall] = []
        errors: list[str] = []
        original = text
        if request.tools:
            try:
                value = json.loads(text)
                if isinstance(value, dict) and "tool_calls" in value:
                    calls, errors = parse_calls(value["tool_calls"])
                    text = str(value.get("text", ""))
            except ValueError:
                if '"tool_calls"' in text:
                    errors.append("Malformed JSON tool proposal")
        return Response(
            text=text,
            tool_calls=calls,
            parse_errors=errors,
            usage=Usage(
                input_tokens=len(tokenizer.encode(prompt)),
                output_tokens=len(tokenizer.encode(original)),
            ),
            latency_ms=(time.perf_counter() - start) * 1000,
            finish_reason="token_budget"
            if len(tokenizer.encode(original)) >= request.max_tokens
            else None,
        )

    async def generate(self, request: Request) -> Response:
        async with self._lock:
            # Keep the lock until the worker finishes even if the caller is cancelled.
            task = asyncio.create_task(asyncio.to_thread(self._generate, request))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise


def make_backend(config: BackendConfig) -> Backend:
    if config.kind == "transformers":
        return TransformersBackend(config)
    return HTTPBackend(config)


def backend_identity(backend: Backend) -> str:
    return type(backend).__name__
