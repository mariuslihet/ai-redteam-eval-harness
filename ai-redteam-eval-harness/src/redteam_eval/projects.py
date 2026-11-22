"""Local project organization; no generated replies or scoring overrides."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from redteam_eval.checkpoint import atomic_json
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.model_checks import strict_json
from redteam_eval.models import Model


class ChecklistItem(Model):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    text: str = Field(min_length=1, max_length=200)
    done: bool = False

    @field_validator("text")
    @classmethod
    def visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Checklist items need text")
        return value.strip()


class ProjectDefinition(Model):
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=6000)
    tags: list[str] = Field(default_factory=list, max_length=8)
    favorite: bool = False
    archived: bool = False
    status: Literal["planning", "active", "review", "finished"] = "planning"
    suite_ids: list[str] = Field(default_factory=list, max_length=10)
    job_ids: list[str] = Field(default_factory=list, max_length=100)
    checklist: list[ChecklistItem] = Field(default_factory=list, max_length=30)

    @field_validator("title")
    @classmethod
    def visible_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Give the project a name")
        return value.strip()

    @field_validator("tags")
    @classmethod
    def distinct_tags(cls, values: list[str]) -> list[str]:
        tags = [value.strip() for value in values]
        if any(not tag or len(tag) > 24 for tag in tags) or len(
            {tag.casefold() for tag in tags}
        ) != len(tags):
            raise ValueError("Use up to eight distinct tags of 1–24 characters")
        return tags

    @field_validator("suite_ids", "job_ids")
    @classmethod
    def distinct_ids(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(
            not re.fullmatch(r"[0-9a-f]{32}", value) for value in values
        ):
            raise ValueError("Linked items must have distinct saved IDs")
        return values

    @model_validator(mode="after")
    def distinct_checklist(self) -> ProjectDefinition:
        if len({item.id for item in self.checklist}) != len(self.checklist):
            raise ValueError("Checklist item IDs must be distinct")
        return self


class SavedProject(ProjectDefinition):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str


class ProjectSaveRequest(ProjectDefinition):
    id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(default=0, ge=0)


class ProjectLaunchRequest(Model):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    queue_revision: int = Field(ge=0)
    suite_revisions: dict[str, int] = Field(max_length=10)


class ProjectStore:
    def __init__(self, root: Path):
        self.store = ReportStore(root)
        self.lock = threading.RLock()

    def path(self) -> Path:
        return self.store.bounded_path(self.store.root / ".projects.json")

    def read(self) -> dict[str, Any]:
        with self.lock:
            path = self.path()
            if not path.exists():
                return {"format_version": 1, "projects": []}
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Project library exceeds 4 MiB")
            data = strict_json(path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or set(data) != {"format_version", "projects"}
                or type(data["format_version"]) is not int
                or data["format_version"] != 1
                or not isinstance(data["projects"], list)
                or len(data["projects"]) > 100
            ):
                raise ValueError("Invalid project library; the existing file was preserved")
            rows = [
                SavedProject.model_validate(row).model_dump(mode="json") for row in data["projects"]
            ]
            if len({row["id"] for row in rows}) != len(rows):
                raise ValueError("Project library contains duplicate IDs")
            return {"format_version": 1, "projects": rows}

    def save(self, request: ProjectSaveRequest) -> dict[str, Any]:
        with self.lock:
            data = self.read()
            previous = next((row for row in data["projects"] if row["id"] == request.id), None)
            if request.id and not previous:
                raise ValueError("Project is missing; refresh the project list")
            if request.expected_revision != (previous["revision"] if previous else 0):
                raise RuntimeError(
                    "Project changed in another window. Reload the saved project; your draft is still in the form."
                )
            if not previous and len(data["projects"]) >= 100:
                raise ValueError(
                    "Project library supports up to 100 projects including archived projects"
                )
            timestamp = datetime.now(UTC).isoformat()
            definition = request.model_dump(mode="json", exclude={"id", "expected_revision"})
            saved = SavedProject(
                **definition,
                id=request.id or uuid.uuid4().hex,
                revision=previous["revision"] + 1 if previous else 1,
                created_at=previous["created_at"] if previous else timestamp,
                updated_at=timestamp,
            ).model_dump(mode="json")
            rows = [saved if row["id"] == saved["id"] else row for row in data["projects"]]
            if not previous:
                rows.append(saved)
            output = {"format_version": 1, "projects": rows}
            if (
                len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                > 4 * 1024 * 1024
            ):
                raise ValueError("Project library exceeds 4 MiB")
            self.store.bounded_path(self.path().with_suffix(".json.tmp"))
            atomic_json(self.path(), output)
            return {"project": saved}
