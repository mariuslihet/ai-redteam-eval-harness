"""Bounded prompt requests and conversation context from actual saved replies."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from redteam_eval.models import Message, Model, Response
from redteam_eval.skill_audit import native_reply


class ModelSelection(Model):
    model: str = Field(min_length=1, max_length=200)


class PromptRequest(ModelSelection):
    action: Literal["prompt"] = "prompt"
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    title: str = Field(default="", max_length=80)
    system_prompt: str = Field(
        default="Answer accurately and clearly. If you are unsure, say so.", max_length=8000
    )
    prompt: str = Field(min_length=1, max_length=12000)
    parent_job_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    seed: int = Field(default=1729, ge=0, le=2**31 - 1)
    max_tokens: int = Field(default=1024, ge=64, le=4096)
    max_total_tokens: int = Field(default=20000, ge=1, le=200000)
    max_run_seconds: float = Field(default=120, ge=1, le=180, allow_inf_nan=False)

    @field_validator("model", "title", "system_prompt", "prompt")
    @classmethod
    def valid_unicode(cls, value: str) -> str:
        value.encode("utf-8")
        return value

    @field_validator("prompt")
    @classmethod
    def visible_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Write a prompt before starting")
        return value


def prompt_messages(request: PromptRequest, parent: dict[str, Any] | None = None) -> list[Message]:
    messages = (
        [Message(role="system", content=request.system_prompt)] if request.system_prompt else []
    )
    if request.parent_job_id:
        if parent is None or parent.get("model") != request.model:
            raise ValueError("Continue with the same saved local model")
        if parent.get("settings", {}).get("system_prompt") != request.system_prompt:
            raise ValueError("Keep the saved system instructions when continuing a conversation")
        original = [Message.model_validate(row) for row in parent.get("messages", [])]
        users = [row for row in original if row.role == "user"]
        if not users or len(users) >= 6:
            raise ValueError("Conversations support up to six replies; start a new prompt")
        response = Response.model_validate(parent.get("response", {}))
        if (
            not response.text.strip()
            or response.truncated
            or response.tool_calls
            or response.parse_errors
            or response.finish_reason not in {"stop", "end_turn", "eos_token", "stop_sequence"}
            or not native_reply(parent["response"], users[-1].content)
        ):
            raise ValueError("Continue only from a complete, unchanged native text reply")
        native_messages = response.request_envelope.get("body", {}).get("messages")
        if (
            native_messages != parent.get("messages")
            or response.model_digest != parent.get("model_digest")
            or response.response_envelope.get("model") != request.model
        ):
            raise ValueError("Saved conversation evidence changed; start a new prompt")
        messages = original + [Message(role="assistant", content=response.text)]
    messages.append(Message(role="user", content=request.prompt))
    if sum(len(row.content.encode("utf-8")) for row in messages) > 32000:
        raise ValueError("Conversation exceeds the 32,000-byte input limit; start a new prompt")
    return messages


def validate_prompt_record(record: Any, request: PromptRequest) -> dict[str, Any]:
    if (
        not isinstance(record, dict)
        or record.get("format_version") != 1
        or record.get("kind") != "prompt_reply"
    ):
        raise ValueError("Saved prompt reply has an unsupported format")
    if (
        PromptRequest.model_validate(record.get("settings", {})) != request
        or record.get("model") != request.model
    ):
        raise ValueError("Saved prompt settings do not match the job")
    messages = [Message.model_validate(row) for row in record.get("messages", [])]
    if not messages or messages[-1].role != "user" or messages[-1].content != request.prompt:
        raise ValueError("Saved prompt input changed")
    if record.get("response") is not None:
        response = Response.model_validate(record["response"])
        if (
            not native_reply(record["response"], request.prompt)
            or response.request_envelope.get("body", {}).get("messages") != record["messages"]
            or response.model_digest != record.get("model_digest")
            or response.response_envelope.get("model") != request.model
        ):
            raise ValueError("Saved prompt reply does not match its native evidence")
    return record
