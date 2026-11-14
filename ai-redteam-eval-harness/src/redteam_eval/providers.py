"""Offline wire-envelope round trips, deterministic jitter and injectable 429 behavior.

Profiles approximate non-streaming chat APIs. They are not vendor conformance emulators.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

from redteam_eval.backends import tool_schema
from redteam_eval.models import Backend, ProviderProfile, Request, Response, ToolCall, derive_seed


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__("Simulated rate limit")
        self.retry_after = retry_after


class ProviderCodec:
    def __init__(self, profile: ProviderProfile):
        self.profile = profile

    def encode_request(self, request: Request) -> dict[str, Any]:
        messages = [m.model_dump() for m in request.messages]
        tools = tool_schema(request)
        common = {"seed": request.seed, "schema": request.schema_}
        if self.profile.name in ("openai", "azure"):
            body: dict[str, Any] = {
                "model": "local-simulated",
                "messages": messages,
                "tools": tools,
                "max_tokens": request.max_tokens,
            }
            if self.profile.name == "azure":
                body["deployment"] = "offline-deployment"
            return {"body": body, "simulation": common}
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        conversation = [m for m in messages if m["role"] != "system"]
        if self.profile.name == "anthropic":
            return {
                "body": {
                    "model": "local-simulated",
                    "system": system,
                    "messages": [
                        {"role": m["role"], "content": [{"type": "text", "text": m["content"]}]}
                        for m in conversation
                    ],
                    "tools": [
                        {"name": t.name, "description": t.description, "input_schema": t.parameters}
                        for t in request.tools
                    ],
                    "max_tokens": request.max_tokens,
                },
                "simulation": common,
            }
        return {
            "body": {
                "modelId": "offline.local",
                "system": [{"text": system}],
                "messages": [
                    {"role": m["role"], "content": [{"text": m["content"]}]} for m in conversation
                ],
                "toolConfig": {
                    "tools": [
                        {
                            "toolSpec": {
                                "name": t.name,
                                "description": t.description,
                                "inputSchema": {"json": t.parameters},
                            }
                        }
                        for t in request.tools
                    ]
                },
                "inferenceConfig": {"maxTokens": request.max_tokens},
            },
            "simulation": common,
        }

    def decode_request(self, envelope: dict[str, Any], original: Request) -> Request:
        """Exercise provider parsing before the canonical local inference call."""
        body = envelope["body"]
        name = self.profile.name
        if name in ("openai", "azure"):
            messages = body["messages"]
        else:
            system = body["system"] if name == "anthropic" else body["system"][0]["text"]
            messages = [{"role": "system", "content": system}]
            for m in body["messages"]:
                content = "".join(b["text"] for b in m["content"])
                messages.append({"role": m["role"], "content": content})
        if name in ("openai", "azure"):
            wire_tools = [t["function"] for t in body["tools"]]
        elif name == "anthropic":
            wire_tools = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                }
                for t in body["tools"]
            ]
        else:
            wire_tools = [
                {
                    "name": t["toolSpec"]["name"],
                    "description": t["toolSpec"]["description"],
                    "parameters": t["toolSpec"]["inputSchema"]["json"],
                }
                for t in body["toolConfig"]["tools"]
            ]
        tools = [
            tool.model_copy(update=wire)
            for tool, wire in zip(original.tools, wire_tools, strict=True)
        ]
        return Request(
            messages=messages,
            tools=tools,
            seed=envelope["simulation"]["seed"],
            max_tokens=original.max_tokens,
            schema=envelope["simulation"]["schema"],
        )

    def encode_response(self, response: Response, seed: int) -> dict[str, Any]:
        name = self.profile.name
        if name in ("openai", "azure"):
            return {
                "id": f"chatcmpl-{seed}",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": response.text,
                            "tool_calls": [
                                {
                                    "id": c.id,
                                    "type": "function",
                                    "function": {
                                        "name": c.name,
                                        "arguments": json.dumps(c.arguments),
                                    },
                                }
                                for c in response.tool_calls
                            ],
                        },
                        "finish_reason": "tool_calls" if response.tool_calls else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                },
            }
        if name == "anthropic":
            return {
                "id": f"msg_{seed}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": response.text}]
                + [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in response.tool_calls
                ],
                "stop_reason": "tool_use" if response.tool_calls else "end_turn",
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": response.text}]
                    + [
                        {"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}}
                        for c in response.tool_calls
                    ],
                }
            },
            "stopReason": "tool_use" if response.tool_calls else "end_turn",
            "usage": {
                "inputTokens": response.usage.input_tokens,
                "outputTokens": response.usage.output_tokens,
                "totalTokens": response.usage.input_tokens + response.usage.output_tokens,
            },
            "metrics": {"latencyMs": response.latency_ms},
        }

    def decode_response(self, envelope: dict[str, Any], original: Response) -> Response:
        name = self.profile.name
        if name in ("openai", "azure"):
            message = envelope["choices"][0]["message"]
            text = message["content"]
            calls = [
                ToolCall(
                    id=c["id"],
                    name=c["function"]["name"],
                    arguments=json.loads(c["function"]["arguments"]),
                )
                for c in message["tool_calls"]
            ]
        elif name == "anthropic":
            text = "".join(c["text"] for c in envelope["content"] if c["type"] == "text")
            calls = [
                ToolCall(id=c["id"], name=c["name"], arguments=c["input"])
                for c in envelope["content"]
                if c["type"] == "tool_use"
            ]
        else:
            blocks = envelope["output"]["message"]["content"]
            text = "".join(c["text"] for c in blocks if "text" in c)
            calls = [
                ToolCall(
                    id=c["toolUse"]["toolUseId"],
                    name=c["toolUse"]["name"],
                    arguments=c["toolUse"]["input"],
                )
                for c in blocks
                if "toolUse" in c
            ]
        return original.model_copy(
            update={"text": text, "tool_calls": calls, "response_envelope": envelope}
        )


class ProviderSimulator:
    def __init__(self, backend: Backend, profile: ProviderProfile):
        self.backend = backend
        self.profile = profile
        self.codec = ProviderCodec(profile)

    async def generate(self, request: Request, attempt: int = 0) -> Response:
        if self.profile.name == "direct":
            return await self.backend.generate(request)
        rng = random.Random(derive_seed(request.seed, self.profile.name, attempt))
        if rng.random() < self.profile.rate_limit_probability:
            raise RateLimited(self.profile.retry_after_ms / 1000)
        jitter = rng.uniform(0, self.profile.jitter_ms)
        await asyncio.sleep(jitter / 1000)
        wire = self.codec.encode_request(request)
        response = await self.backend.generate(self.codec.decode_request(wire, request))
        response.latency_ms += jitter
        envelope = self.codec.encode_response(response, request.seed)
        response = self.codec.decode_response(envelope, response)
        response.request_envelope = wire
        return response
