"""Versioned local test-suite definitions, separate from measured model results."""

from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from redteam_eval.checkpoint import atomic_json
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import ModelCheckRequest
from redteam_eval.model_checks import check_json_depth
from redteam_eval.models import Model


class SuiteDefinition(Model):
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=8)
    favorite: bool = False
    archived: bool = False
    settings: ModelCheckRequest

    @field_validator("title")
    @classmethod
    def visible_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give the suite a name")
        return value.strip()

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        tags = [value.strip() for value in values]
        if any(not tag or len(tag) > 24 for tag in tags):
            raise ValueError("Each tag needs 1–24 characters")
        if len({tag.casefold() for tag in tags}) != len(tags):
            raise ValueError("Use distinct tags")
        return tags

    @model_validator(mode="after")
    def standalone_settings(self) -> SuiteDefinition:
        if self.settings.retry_of is not None:
            raise ValueError("Suite definitions cannot claim a saved-run retry relationship")
        return self


class SuiteSaveRequest(SuiteDefinition):
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(default=0, ge=0, le=1_000_000)


class SavedSuite(SuiteDefinition):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class SuiteLibrary:
    def __init__(self, root: Path):
        self.store = ReportStore(root)
        self.lock = threading.RLock()

    def path(self) -> Path:
        return self.store.bounded_path(self.store.root / ".test-suites.json")

    def read(self) -> dict[str, Any]:
        with self.lock:
            path = self.path()
            suites = []
            if path.exists():
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("Suite library exceeds its 8 MiB limit")
                content = path.read_text(encoding="utf-8")
                check_json_depth(content)
                data = json.loads(content)
                if (
                    not isinstance(data, dict)
                    or set(data) != {"format_version", "suites"}
                    or data["format_version"] != 1
                ):
                    raise ValueError("Suite library format is invalid; existing file was preserved")
                if not isinstance(data["suites"], list) or len(data["suites"]) > 100:
                    raise ValueError("Suite library needs at most 100 saved definitions")
                suites = [
                    SavedSuite.model_validate(row).model_dump(mode="json") for row in data["suites"]
                ]
                if len({row["id"] for row in suites}) != len(suites):
                    raise ValueError("Suite library contains duplicate IDs")
            return {
                "format_version": 1,
                "suites": copy.deepcopy(suites),
                "inference_requested": False,
            }

    def save(self, request: SuiteSaveRequest) -> dict[str, Any]:
        with self.lock:
            data = self.read()
            previous = next((row for row in data["suites"] if row["id"] == request.id), None)
            if request.id is not None and previous is None:
                raise ValueError("Saved suite is missing; reload the library")
            if request.expected_revision != (previous["revision"] if previous else 0):
                raise RuntimeError(
                    "This suite changed in another window. Reload it before saving; your draft is still in the form."
                )
            if previous is None and len(data["suites"]) >= 100:
                raise ValueError(
                    "The library supports at most 100 suites, including archived suites"
                )
            timestamp = datetime.now(UTC).isoformat()
            definition = SuiteDefinition.model_validate(
                request.model_dump(exclude={"id", "expected_revision"})
            ).model_dump(mode="json")
            saved = SavedSuite(
                **definition,
                id=request.id or uuid.uuid4().hex,
                revision=previous["revision"] + 1 if previous else 1,
                created_at=previous["created_at"] if previous else timestamp,
                updated_at=timestamp,
            ).model_dump(mode="json")
            rows = [saved if row["id"] == saved["id"] else row for row in data["suites"]]
            if previous is None:
                rows.append(saved)
            output = {"format_version": 1, "suites": rows}
            if (
                len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                > 8 * 1024 * 1024
            ):
                raise ValueError("Suite library exceeds its 8 MiB limit")
            self.store.bounded_path(self.path().with_suffix(".json.tmp"))
            atomic_json(self.path(), output)
            return {"suite": saved, "inference_requested": False}
