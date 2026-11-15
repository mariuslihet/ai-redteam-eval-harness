"""Recover exact user-message attack plans; never reuse recorded target completions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from redteam_eval.archive import RunArchive
from redteam_eval.models import RunConfig, Scenario, Turn
from redteam_eval.mutations import Mutation


def text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(
        isinstance(v, dict) and isinstance(v.get("text"), str) for v in value
    ):
        return "".join(v["text"] for v in value)
    raise ValueError("Recorded user message has an unsupported content representation")


def user_segments(turn: Turn) -> list[str]:
    """Prefer explicit segment boundaries; recover legacy native/provider message bodies."""
    if turn.user_messages:
        segments = list(turn.user_messages)
    else:
        body = turn.response.request_envelope.get("body", {})
        messages = body.get("messages") if isinstance(body, dict) else None
        if isinstance(messages, list) and messages:
            if not all(isinstance(v, dict) and isinstance(v.get("role"), str) for v in messages):
                raise ValueError("Recorded request has malformed conversation messages")
            assistant_positions = [i for i, v in enumerate(messages) if v["role"] == "assistant"]
            # First requests include one context message after the optional system message.
            start = (
                assistant_positions[-1] + 1
                if assistant_positions
                else (2 if messages[0]["role"] == "system" else 1)
            )
            tail = messages[start:]
            if not tail or any(v["role"] != "user" for v in tail):
                raise ValueError("Recorded request does not contain a complete user-message suffix")
            segments = [text_content(v.get("content")) for v in tail]
        elif turn.mutation != "split":
            segments = [turn.prompt]
        else:
            raise ValueError("Cannot replay a split mutation without recorded message boundaries")
    if not segments or any(not value for value in segments) or "\n".join(segments) != turn.prompt:
        raise ValueError("Recorded user-message boundaries do not match the turn prompt")
    return segments


def attack_plans(source: RunArchive) -> dict[str, list[Mutation]]:
    plans = {}
    for pair in source.results:
        mutations = []
        for turn in pair.baseline.turns:
            segments = user_segments(turn)
            mutations.append(
                Mutation(
                    operator=turn.mutation,
                    prompt=segments[-1],
                    prelude=segments[:-1],
                    score=turn.strategy_score,
                )
            )
        plans[pair.key] = mutations
    return plans


def plan_digest(source: RunArchive) -> str:
    plans = attack_plans(source)
    payload = {
        key: [mutation.model_dump() for mutation in value] for key, value in sorted(plans.items())
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_replay(
    source: RunArchive, config: RunConfig, scenarios: list[Scenario]
) -> dict[str, list[Mutation]]:
    for field in ("seed", "cases_per_family", "max_turns", "max_tokens", "providers"):
        if getattr(source.config, field) != getattr(config, field):
            raise ValueError(f"Replay requires the recorded {field}")
    if config.replay_plan_digest != plan_digest(source):
        raise ValueError("Replay plan digest does not match the supplied measured archive")
    old_scenarios = {pair.scenario.id: pair.scenario for pair in source.results}
    if {scenario.id: scenario for scenario in scenarios} != old_scenarios:
        raise ValueError("Replay generated worlds differ from the recorded scenarios")
    return attack_plans(source)
