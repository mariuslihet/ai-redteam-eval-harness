"""Human organization details kept separate from immutable measured job evidence."""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from redteam_eval.checkpoint import atomic_json
from redteam_eval.dashboard_data import ReportStore
from redteam_eval.model_checks import strict_json
from redteam_eval.models import Model


class JobDetails(Model):
    title: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=4000)
    favorite: bool = False
    archived: bool = False

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
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


class SavedJobDetails(JobDetails):
    updated_at: str


class DetailsRequest(JobDetails):
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(ge=0)


class ReferenceRequest(Model):
    job_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(ge=0)


class JobDetailsStore:
    def __init__(self, root: Path):
        self.store = ReportStore(root)
        self.lock = threading.RLock()

    def path(self) -> Path:
        return self.store.bounded_path(self.store.root / ".job-details.json")

    def read(self) -> dict[str, Any]:
        with self.lock:
            path = self.path()
            result: dict[str, Any] = {
                "format_version": 1,
                "revision": 0,
                "reference_job_id": None,
                "records": {},
            }
            if not path.exists():
                return result
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("Saved job details exceed 8 MiB")
            content = path.read_text(encoding="utf-8")
            data = strict_json(content)
            if (
                not isinstance(data, dict)
                or set(data) != set(result)
                or type(data["format_version"]) is not int
                or data["format_version"] != 1
            ):
                raise ValueError(
                    "Saved job details have an unsupported format; the file was preserved"
                )
            if type(data["revision"]) is not int or data["revision"] < 0:
                raise ValueError("Invalid saved job details revision")
            if data["reference_job_id"] is not None and not re.fullmatch(
                r"[0-9a-f]{32}", str(data["reference_job_id"])
            ):
                raise ValueError("Invalid reference job ID")
            if not isinstance(data["records"], dict) or len(data["records"]) > 1000:
                raise ValueError("Job details support up to 1,000 records")
            for identifier, record in data["records"].items():
                if not re.fullmatch(r"[0-9a-f]{32}", identifier):
                    raise ValueError("Invalid saved job ID")
                data["records"][identifier] = SavedJobDetails.model_validate(record).model_dump(
                    mode="json"
                )
            return data

    def _write(self, data: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        if data["revision"] != expected_revision:
            raise RuntimeError(
                "Saved job details changed in another window. Reload saved details before saving; your draft is still in the form."
            )
        if (
            len(data["records"]) > 1000
            or len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            > 8 * 1024 * 1024
        ):
            raise ValueError("Job details exceed their storage limit")
        data["revision"] += 1
        self.store.bounded_path(self.path().with_suffix(".json.tmp"))
        atomic_json(self.path(), data)
        return data

    def save(self, request: DetailsRequest) -> dict[str, Any]:
        with self.lock:
            data = self.read()
            values = request.model_dump(mode="json", exclude={"job_id", "expected_revision"})
            data["records"][request.job_id] = {
                **values,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            return self._write(data, request.expected_revision)

    def reference(self, request: ReferenceRequest) -> dict[str, Any]:
        with self.lock:
            data = self.read()
            data["reference_job_id"] = request.job_id
            return self._write(data, request.expected_revision)
