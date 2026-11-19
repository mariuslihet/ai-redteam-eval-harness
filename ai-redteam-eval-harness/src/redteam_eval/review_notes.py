"""Separate, versioned human notes bound to exact saved question/reply evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from redteam_eval.checkpoint import atomic_json
from redteam_eval.models import Model


class NoteRequest(Model):
    question_id: str = Field(min_length=1, max_length=120)
    decision: Literal["pending", "model_error", "checker_issue", "acceptable"] = "pending"
    note: str = Field(default="", max_length=4000)
    expected_revision: int = Field(default=0, ge=0, le=1_000_000)


class SavedNote(Model):
    decision: Literal["pending", "model_error", "checker_issue", "acceptable"]
    note: str = Field(max_length=4000)
    revision: int = Field(ge=1)
    updated_at: str
    question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def load_notes(path: Path, job_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"format_version": 1, "job_id": job_id, "notes": {}}
    if path.stat().st_size > 1_048_576:
        raise ValueError("Review notes file is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("format_version") != 1
        or data.get("job_id") != job_id
        or not isinstance(data.get("notes"), dict)
        or len(data["notes"]) > 1000
    ):
        raise ValueError("Review notes do not match this saved test")
    return {
        "format_version": 1,
        "job_id": job_id,
        "notes": {
            identifier: SavedNote.model_validate(note).model_dump(mode="json")
            for identifier, note in data["notes"].items()
        },
    }


def read_notes(path: Path, job_id: str, record: dict[str, Any]) -> dict[str, Any]:
    data = load_notes(path, job_id)
    rows = {row["question"]["id"]: row for row in record["results"]}
    result = {}
    for identifier, note in data["notes"].items():
        row = rows.get(identifier)
        stale = (
            row is None
            or note["question_sha256"] != fingerprint(row["question"])
            or note["response_sha256"] != fingerprint(row["response"])
        )
        result[identifier] = {**note, "stale": stale}
    return {
        "format_version": 1,
        "job_id": job_id,
        "notes": result,
        "inference_requested": False,
        "source_modified": False,
        "interpretation": "These are separate human review notes, not scoring overrides. Original replies and automated grades remain unchanged. Notes are marked stale if the saved question or reply changed.",
    }


def save_note(
    path: Path, job_id: str, record: dict[str, Any], request: NoteRequest
) -> dict[str, Any]:
    row = next(
        (row for row in record["results"] if row["question"]["id"] == request.question_id), None
    )
    if row is None:
        raise ValueError("Review notes must refer to an actual saved question")
    data = load_notes(path, job_id)
    previous = data["notes"].get(request.question_id)
    revision = previous["revision"] if previous else 0
    if request.expected_revision != revision:
        raise RuntimeError(
            "This note changed in another session. Reload it before saving; your text has not been overwritten."
        )
    note = SavedNote(
        decision=request.decision,
        note=request.note,
        revision=revision + 1,
        updated_at=datetime.now(UTC).isoformat(),
        question_sha256=fingerprint(row["question"]),
        response_sha256=fingerprint(row["response"]),
    )
    data["notes"][request.question_id] = note.model_dump(mode="json")
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > 1_048_576:
        raise ValueError("Review notes exceed the file size limit; shorten the note before saving")
    atomic_json(path, data)
    return read_notes(path, job_id, record)
