"""Bounded, exact custom test definitions; never manufacture model replies."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, field_validator

from redteam_eval.model_checks import SkillQuestion, finite_number, strict_json
from redteam_eval.models import Model


class CustomCase(Model):
    name: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=8000)
    answer_type: Literal["integer", "text", "json"]
    expected: str = Field(min_length=1, max_length=4000)

    @field_validator("name", "prompt", "expected")
    @classmethod
    def visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Names, prompts and expected answers need visible text")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Text contains an invalid Unicode character")
        return value

    def question(self) -> SkillQuestion:
        expected: Any = self.expected.strip()
        if self.answer_type != "text":
            expected = strict_json(expected)
            if self.answer_type == "integer" and type(expected) is not int:
                raise ValueError("An integer answer must contain one JSON integer")
            if self.answer_type == "integer":
                finite_number(str(expected))
            pending = [(expected, 0)]
            while pending:
                value, depth = pending.pop()
                if isinstance(value, (dict, list)) and depth >= 32:
                    raise ValueError(
                        "Custom JSON expected answers support at most 32 nested containers"
                    )
                if isinstance(value, float):
                    raise ValueError(
                        "Exact JSON checks support whole numbers; encode decimals as strings"
                    )
                if isinstance(value, dict):
                    pending.extend((item, depth + 1) for item in value.values())
                elif isinstance(value, list):
                    pending.extend((item, depth + 1) for item in value)
        contract = {
            "name": self.name,
            "prompt": self.prompt,
            "type": self.answer_type,
            "expected": expected,
        }
        digest = hashlib.sha256(
            json.dumps(contract, sort_keys=True, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        instruction = {
            "integer": "Reply with one integer only, without explanation.",
            "text": "Reply with the requested text only, without explanation.",
            "json": "Reply with one JSON value only, without Markdown or explanation.",
        }[self.answer_type]
        return SkillQuestion(
            id=f"custom-{digest}",
            skill="Custom tasks",
            prompt=f"{self.prompt}\n\n{instruction}",
            expected=expected,
            answer_type=self.answer_type,
            rule=f"{self.name}: author-supplied expected answer, not independently verified. "
            "Compare exact values and types; ignore outer whitespace. " + instruction,
        )


def custom_questions(cases: list[dict[str, Any]] | None) -> list[SkillQuestion]:
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise ValueError("Supply one to twenty custom test definitions")
    if len(json.dumps(cases, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 48000:
        raise ValueError("Custom definitions exceed the 48,000-byte limit")
    definitions = [CustomCase.model_validate(case) for case in cases]
    names = [case.name.strip().casefold() for case in definitions]
    if len(set(names)) != len(names):
        raise ValueError("Give each custom test a distinct name")
    return [case.question() for case in definitions]
