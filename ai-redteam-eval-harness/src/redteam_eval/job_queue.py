"""Persistent, explicitly started queue for actual local model-skill jobs."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from redteam_eval.checkpoint import atomic_json
from redteam_eval.dashboard_jobs import DashboardJob, JobManager, ModelCheckRequest, now
from redteam_eval.model_checks import check_json_depth
from redteam_eval.models import Model


class QueueEntry(Model):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    title: str = Field(min_length=1, max_length=100)
    request: ModelCheckRequest
    question_count: int = Field(ge=1, le=80)
    project_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    project_title: str | None = Field(default=None, max_length=80)
    project_revision: int | None = Field(default=None, ge=1)
    suite_id: str | None = None
    suite_revision: int | None = None
    status: Literal[
        "pending", "starting", "running", "complete", "failed", "cancelled", "interrupted"
    ] = "pending"
    job_id: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    saved_reply_count: int | None = Field(default=None, ge=0)


class QueueState(Model):
    format_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    paused: bool = True
    pause_on_error: bool = True
    message: str = "Add work, then start the queue when ready."
    items: list[QueueEntry] = Field(default_factory=list, max_length=100)


class QueueAction(Model):
    expected_revision: int = Field(ge=0)
    action: Literal[
        "start",
        "pause",
        "move_up",
        "move_down",
        "cancel",
        "repeat",
        "clear_finished",
        "set_error_policy",
        "stop_current",
    ]
    item_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    pause_on_error: bool | None = None


class QueueAdd(Model):
    expected_revision: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=100)
    settings: ModelCheckRequest


class SuiteSelection(Model):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)


class QueueBatch(Model):
    expected_revision: int = Field(ge=0)
    suites: list[SuiteSelection] = Field(min_length=1, max_length=10)


class JobQueue:
    def __init__(self, manager: JobManager):
        self.manager = manager
        self.state = QueueState()
        self.load_error: str | None = None
        try:
            path = self.path()
            if path.exists():
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("Saved queue exceeds 8 MiB")
                text = path.read_text(encoding="utf-8")
                check_json_depth(text)
                self.state = QueueState.model_validate_json(text)
                ids = [item.id for item in self.state.items]
                if len(set(ids)) != len(ids):
                    raise ValueError("Saved queue contains duplicate item IDs")
                for item in self.state.items:
                    if (
                        item.request.retry_of is not None
                        or item.question_count != item.request.question_count()
                    ):
                        raise ValueError("Saved queue settings do not match their question plan")
                    if item.status in ("starting", "running"):
                        job = manager.jobs.get(item.job_id or "")
                        item.status = (
                            job.status
                            if job and job.status in ("complete", "failed", "cancelled")
                            else "interrupted"
                        )
                        item.error = (
                            job.error if job else None
                        ) or "App restarted; inspect saved replies before repeating."
                self.state.paused = True
                self.state.message = (
                    "Saved queue restored paused. Review it, then start when ready."
                )
        except (ValueError, OSError) as exc:
            self.load_error = f"Queue could not be loaded; the existing file was preserved: {exc}"
        manager.on_idle = self._on_idle

    def path(self) -> Path:
        return self.manager.store.bounded_path(self.manager.store.root / ".job-queue.json")

    def _save(self) -> None:
        output = self.state.model_dump(mode="json")
        if (
            len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            > 8 * 1024 * 1024
        ):
            raise ValueError("Queue exceeds its 8 MiB storage limit")
        self.manager.store.bounded_path(self.path().with_suffix(".json.tmp"))
        self.state.revision += 1
        try:
            atomic_json(self.path(), self.state.model_dump(mode="json"))
        except OSError as exc:
            self.state.paused = True
            self.load_error = f"Queue paused because its state could not be saved: {exc}"
            raise

    def _check(self, revision: int) -> None:
        if self.load_error:
            raise ValueError(self.load_error)
        if revision != self.state.revision:
            raise RuntimeError("The queue changed. Refresh it before changing its contents.")

    def snapshot(self) -> dict[str, Any]:
        with self.manager.lock:
            items = []
            for item in self.state.items:
                job = self.manager.jobs.get(item.job_id or "")
                replies = (
                    len((job.result or {}).get("results", []))
                    if job
                    else item.saved_reply_count
                    if item.job_id
                    else 0
                )
                items.append(
                    {
                        **item.model_dump(mode="json", exclude={"request"}),
                        "status": ("starting" if job.status == "queued" else job.status)
                        if job
                        else item.status,
                        "setup": item.request.model_dump(
                            mode="json", exclude={"custom_cases", "question_ids", "selected_skills"}
                        ),
                        "saved_replies": replies,
                    }
                )
            return {
                **self.state.model_dump(mode="json", exclude={"items"}),
                "items": items,
                "load_error": self.load_error,
                "active_job_id": self.manager.active,
                "pending_questions": sum(
                    item.question_count for item in self.state.items if item.status == "pending"
                ),
            }

    def add(self, definitions: list[dict[str, Any]], revision: int) -> dict[str, Any]:
        with self.manager.lock:
            self._check(revision)
            if not 1 <= len(definitions) <= 10:
                raise ValueError("Add one to ten suite setups at a time")
            entries = []
            for definition in definitions:
                request = ModelCheckRequest.model_validate(definition["settings"])
                if request.retry_of is not None:
                    raise ValueError(
                        "Queued setups must be independent runs, without a retry relationship"
                    )
                entries.append(
                    QueueEntry(
                        id=uuid.uuid4().hex,
                        title=definition["title"].strip(),
                        request=request,
                        question_count=request.question_count(),
                        project_id=definition.get("project_id"),
                        project_title=definition.get("project_title"),
                        project_revision=definition.get("project_revision"),
                        suite_id=definition.get("suite_id"),
                        suite_revision=definition.get("suite_revision"),
                        created_at=now(),
                    )
                )
            if (
                sum(item.status == "pending" for item in self.state.items) + len(entries) > 20
                or len(self.state.items) + len(entries) > 100
            ):
                raise ValueError(
                    "Queue supports 20 pending items and 100 history entries. Clear finished queue history to make room."
                )
            self.state.items.extend(entries)
            self._save()
            self._kick()
            return self.snapshot()

    def action(self, request: QueueAction) -> dict[str, Any]:
        with self.manager.lock:
            self._check(request.expected_revision)
            item = next((row for row in self.state.items if row.id == request.item_id), None)
            if request.action in ("move_up", "move_down", "cancel", "repeat") and item is None:
                raise ValueError("Choose an existing queue item")
            if request.action in ("move_up", "move_down", "cancel"):
                if item is None or item.status != "pending":
                    raise ValueError("Only pending work can be reordered or removed from the queue")
                if request.action == "cancel":
                    item.status, item.finished_at = "cancelled", now()
                    item.error = "Removed before inference; no model job was started."
                else:
                    pending = [row for row in self.state.items if row.status == "pending"]
                    position = pending.index(item)
                    other = position + (-1 if request.action == "move_up" else 1)
                    if not 0 <= other < len(pending):
                        raise ValueError("Item is already at this end of the pending queue")
                    left, right = (
                        self.state.items.index(item),
                        self.state.items.index(pending[other]),
                    )
                    self.state.items[left], self.state.items[right] = (
                        self.state.items[right],
                        self.state.items[left],
                    )
            elif request.action == "repeat":
                if item is None:
                    raise ValueError("Choose a queue item")
                return self.add(
                    [
                        {
                            "title": item.title,
                            "settings": item.request.model_dump(mode="json"),
                            "suite_id": item.suite_id,
                            "suite_revision": item.suite_revision,
                            "project_id": item.project_id,
                            "project_title": item.project_title,
                            "project_revision": item.project_revision,
                        }
                    ],
                    self.state.revision,
                )
            elif request.action == "stop_current":
                self.state.paused = True
                self.state.message = (
                    "Queue paused; stopping the current job preserves completed replies."
                )
                self._save()
                if self.manager.active is not None:
                    self.manager.cancel(self.manager.active)
                return self.snapshot()
            elif request.action == "clear_finished":
                self.state.items = [
                    row
                    for row in self.state.items
                    if row.status in ("pending", "starting", "running")
                    or (
                        row.job_id in self.manager.jobs
                        and self.manager.jobs[row.job_id].status in ("queued", "running")
                    )
                ]
            elif request.action == "set_error_policy":
                if request.pause_on_error is None:
                    raise ValueError("Choose whether the queue should pause after errors")
                self.state.pause_on_error = request.pause_on_error
            else:
                self.state.paused = request.action == "pause"
                self.state.message = (
                    "Queue paused. The current job may finish."
                    if self.state.paused
                    else "Queue running. Items start one at a time."
                )
            self._save()
            self._kick()
            return self.snapshot()

    def _kick(self) -> None:
        while not self.state.paused and not self.load_error and self.manager.active is None:
            item = next((row for row in self.state.items if row.status == "pending"), None)
            if item is None:
                return
            item.status, item.started_at = "starting", now()
            self._save()
            try:
                job = (
                    self.manager.submit(
                        item.request.model_dump(mode="json"),
                        project_id=item.project_id,
                        project_title=item.project_title,
                        project_revision=item.project_revision,
                    )
                    if item.project_id
                    else self.manager.submit(item.request.model_dump(mode="json"))
                )
                item.job_id, item.status = job.id, "running"
                self._save()
                return
            except (ValueError, RuntimeError, KeyError) as exc:
                item.status, item.error, item.finished_at = "failed", str(exc), now()
                if self.state.pause_on_error:
                    self.state.paused, self.state.message = (
                        True,
                        "Queue paused because a job could not start.",
                    )
                self._save()

    def _on_idle(self, job: DashboardJob) -> None:
        with self.manager.lock:
            if self.load_error:
                return
            try:
                item = next(
                    (row for row in self.state.items if row.job_id == job.id),
                    None,
                )
                if item and job.status in ("complete", "failed", "cancelled"):
                    item.status, item.error, item.finished_at = job.status, job.error, now()
                    item.saved_reply_count = len((job.result or {}).get("results", []))
                    if self.state.pause_on_error and job.status in ("failed", "cancelled"):
                        self.state.paused, self.state.message = (
                            True,
                            "Queue paused after a failed or stopped job. Review its saved replies.",
                        )
                    self._save()
                self._kick()
            except (OSError, ValueError, RuntimeError) as exc:
                self.state.paused = True
                self.load_error = f"Queue paused after a dispatch or persistence problem: {exc}"

    def shutdown(self) -> None:
        with self.manager.lock:
            self.state.paused = True
            self.state.message = "Queue paused because the dashboard closed."
            if not self.load_error and (self.state.items or self.path().exists()):
                self._save()
