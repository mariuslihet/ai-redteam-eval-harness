"""Bounded, inference-free audit of saved native model answers."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from redteam_eval.model_checks import check_json_depth, review_skill_results

CATEGORIES = (
    "corrected_failures",
    "unconfirmed_failures",
    "format_only_failures",
    "confirmed_failures",
    "previous_pass_now_failed",
    "saved_pass_needs_review",
    "still_passing",
)


PRIORITIES = {
    "corrected_failures": 0,
    "unconfirmed_failures": 1,
    "saved_pass_needs_review": 1,
    "previous_pass_now_failed": 2,
    "format_only_failures": 3,
    "confirmed_failures": 4,
    "still_passing": 4,
}


def example_priority(row: dict[str, Any]) -> int:
    priority = PRIORITIES[row["category"]]
    return min(priority, 1) if row["classification_changed"] else priority


def audit_category(previous_passed: bool, grade: dict[str, Any]) -> str:
    uncertain = grade["status"] in {"incomplete", "needs_review"}
    if previous_passed:
        return (
            "saved_pass_needs_review"
            if uncertain
            else "still_passing"
            if grade["passed"]
            else "previous_pass_now_failed"
        )
    if grade["passed"]:
        return "corrected_failures"
    if uncertain:
        return "unconfirmed_failures"
    if grade["answer_correct"] is True and grade["format_correct"] is False:
        return "format_only_failures"
    return "confirmed_failures"


def native_reply(response: dict[str, Any], prompt: str) -> bool:
    envelope = response.get("response_envelope")
    if not isinstance(envelope, dict) or not isinstance(envelope.get("model"), str):
        return False
    message = envelope.get("message")
    if not isinstance(message, dict):
        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return False
        message = choices[0].get("message")
    request = response.get("request_envelope")
    body = request.get("body") if isinstance(request, dict) else None
    messages = body.get("messages") if isinstance(body, dict) else None
    users = (
        [row for row in messages if isinstance(row, dict) and row.get("role") == "user"]
        if isinstance(messages, list)
        else []
    )
    return (
        isinstance(message, dict)
        and isinstance(message.get("content"), str)
        and message["content"] == response.get("text")
        and (response.get("raw_text") is None or response.get("raw_text") == message["content"])
        and bool(users)
        and users[-1].get("content") == prompt
    )


def saved_report_paths(root: Path, state: dict[str, Any]) -> Iterator[Path]:
    visited = 0
    for directory, names, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > 2000:
            state["truncated"] = True
            return
        current = Path(directory)
        names[:] = sorted(
            name
            for name in names
            if not (current / name).is_symlink()
            and not getattr(current / name, "is_junction", lambda: False)()
            and (current / name).resolve().is_relative_to(root)
        )
        if "model-checks.json" in files:
            yield current / "model-checks.json"


def audit_saved_skills(root: Path, max_files: int = 200, max_replies: int = 5000) -> dict[str, Any]:
    if (
        type(max_files) is not int
        or not 1 <= max_files <= 500
        or type(max_replies) is not int
        or not 1 <= max_replies <= 10000
    ):
        raise ValueError("Choose 1–500 files and 1–10000 replies for an audit")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("Choose an existing reports directory")
    state: dict[str, Any] = {
        "format_version": 1,
        "files_considered": 0,
        "files_audited": 0,
        "duplicate_files_skipped": 0,
        "audited_replies": 0,
        "unverified_replies": 0,
        "classification_changes": 0,
        "review_candidates": 0,
        "bytes_read": 0,
        "truncated": False,
        "counts": {name: 0 for name in CATEGORIES},
        "files": [],
        "issues": [],
        "examples": [],
        "limits": {
            "files": max_files,
            "replies": max_replies,
            "file_bytes": 8 * 1024 * 1024,
            "total_bytes": 32 * 1024 * 1024,
            "examples": 50,
        },
        "inference_requested": False,
        "source_modified": False,
    }
    seen = set()
    for path in saved_report_paths(root, state):
        if state["files_considered"] >= max_files or state["audited_replies"] >= max_replies:
            state["truncated"] = True
            break
        state["files_considered"] += 1
        label = path.relative_to(root).as_posix()
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Linked reports outside this audit scope are excluded")
            size = path.stat().st_size
            if size > state["limits"]["file_bytes"]:
                raise ValueError("Saved report exceeds the audit file-size limit")
            if state["bytes_read"] + size > state["limits"]["total_bytes"]:
                state["truncated"] = True
                break
            with path.open("rb") as stream:
                raw = stream.read(
                    min(
                        state["limits"]["file_bytes"],
                        state["limits"]["total_bytes"] - state["bytes_read"],
                    )
                    + 1
                )
            state["bytes_read"] += len(raw)
            if (
                len(raw) > state["limits"]["file_bytes"]
                or state["bytes_read"] > state["limits"]["total_bytes"]
            ):
                state["truncated"] = True
                raise ValueError("Report changed beyond the audit size limit")
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                state["duplicate_files_skipped"] += 1
                continue
            check_json_depth(raw.decode("utf-8-sig"))
            record = json.loads(raw)
            if not isinstance(record, dict):
                raise ValueError("Saved report must be a JSON object")
            reviewed = review_skill_results(record)
            identifiers = [row["question"]["id"] for row in reviewed["results"]]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("Duplicate question IDs make the saved report ambiguous")
            if state["audited_replies"] + len(reviewed["results"]) > max_replies:
                state["truncated"] = True
                break
            seen.add(digest)
            state["files_audited"] += 1
            state["files"].append(
                {
                    "source": label,
                    "snapshot_sha256": digest,
                    "saved_replies": len(reviewed["results"]),
                }
            )
            for original, row in zip(record["results"], reviewed["results"], strict=True):
                if not native_reply(row["response"], row["question"]["prompt"]):
                    state["unverified_replies"] += 1
                    continue
                grade = row["grade"]
                category = audit_category(row["previous_passed"], grade)
                state["audited_replies"] += 1
                state["counts"][category] += 1
                old_status = original["grade"].get("status")
                changed = isinstance(old_status, str) and old_status != grade["status"]
                state["classification_changes"] += int(changed)
                if changed or category != "still_passing":
                    state["review_candidates"] += 1
                if changed or category != "still_passing":
                    state["examples"].append(
                        {
                            "source": label,
                            "snapshot_sha256": digest,
                            "model": record.get("model"),
                            "question": row["question"],
                            "actual_reply": row["response"].get("text", ""),
                            "saved_passed": row["previous_passed"],
                            "saved_status": old_status,
                            "category": category,
                            "classification_changed": changed,
                            "current_grade": grade,
                        }
                    )
            state["examples"].sort(key=example_priority)
            state["examples"] = state["examples"][:50]
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
            state["issues"].append({"source": label, "error": str(exc)})
    state["examples_omitted"] = max(0, state["review_candidates"] - len(state["examples"]))
    state["interpretation"] = (
        "This is a bounded review of actual saved native replies, not a general false-positive-rate estimate. Exact duplicate file contents count once. Corrected failures now pass the current checker. Incomplete replies and checker limits remain unconfirmed, not proven wrong answers. Correct content in a forbidden format still fails the requested format and is not labeled a false positive. Missing or inconsistent native task/reply evidence is reported separately; wrapper-modified replies are excluded. Source hashes identify the snapshots read; no source is rewritten and no model is called. Examples prioritize corrected and uncertain cases, with at most 50 shown. Errors and scan limits reduce the audited scope."
    )
    return state
