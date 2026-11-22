"""Loopback-only measured-results dashboard and same-origin local inference API."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import Field, ValidationError

from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import JobManager, ModelCheckRequest
from redteam_eval.inspection import inspect_pair
from redteam_eval.job_details import DetailsRequest, JobDetailsStore, ReferenceRequest
from redteam_eval.job_queue import JobQueue, QueueAction, QueueAdd, QueueBatch
from redteam_eval.model_workspace import ModelSelection
from redteam_eval.models import BackendConfig, Model, Thresholds
from redteam_eval.projects import ProjectLaunchRequest, ProjectSaveRequest, ProjectStore
from redteam_eval.regression import RegressionLimits, compare_runs
from redteam_eval.reporting import gate, summarize
from redteam_eval.review_notes import NoteRequest
from redteam_eval.skill_audit import audit_saved_skills
from redteam_eval.skill_consistency import SkillConsistencyRequest
from redteam_eval.skill_tools import build_skill_plan
from redteam_eval.suite_library import SuiteLibrary, SuiteSaveRequest


class HTTPReply(Model):
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = {}


class GatePreviewRequest(Model):
    run_id: str = Field(pattern=r"^run_[0-9a-f]{24}$")
    thresholds: Thresholds


class SkillComparisonRequest(Model):
    previous: str = Field(pattern=r"^[0-9a-f]{32}$")
    current: str = Field(pattern=r"^[0-9a-f]{32}$")


class SkillRetryRequest(Model):
    model: str | None = Field(default=None, min_length=1, max_length=200)


def json_reply(value: Any, status: int = 200) -> HTTPReply:
    return HTTPReply(
        status=status, body=json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    )


class DashboardApp:
    def __init__(self, root: Path, backend: BackendConfig, port: int = 8765):
        self.store = ReportStore(root)
        self.jobs = JobManager(self.store, backend)
        self.suites = SuiteLibrary(self.store.root)
        self.queue = JobQueue(self.jobs)
        self.job_details = JobDetailsStore(self.store.root)
        self.projects = ProjectStore(self.store.root)
        self.port, self.csrf = port, secrets.token_urlsafe(32)

    def handle(
        self, method: str, target: str, headers: dict[str, str], body: bytes = b""
    ) -> HTTPReply:
        headers = {key.lower(): value for key, value in headers.items()}
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if headers.get("host") not in hosts:
            return json_reply(
                {"error": "Dashboard accepts only its loopback host", "kind": "permission"}, 403
            )
        origin = headers.get("origin")
        if origin is not None and origin not in {"http://" + host for host in hosts}:
            return json_reply(
                {"error": "Cross-origin dashboard requests are disabled", "kind": "permission"}, 403
            )
        if method not in ("GET", "POST"):
            return json_reply({"error": "Method not supported"}, 405)
        if method == "POST":
            if origin is None or not secrets.compare_digest(
                headers.get("x-dashboard-token", ""), self.csrf
            ):
                return json_reply(
                    {
                        "error": "Dashboard session changed. Refresh the page and retry.",
                        "kind": "permission",
                    },
                    403,
                )
            if headers.get("content-type", "").split(";", 1)[0] != "application/json":
                return json_reply({"error": "Requests must use application/json"}, 415)
        if len(body) > 65536:
            return json_reply({"error": "Request body is too large"}, 413)
        try:
            parsed = urlsplit(target)
        except ValueError:
            return json_reply({"error": "Malformed request path"}, 400)
        route = unquote(parsed.path)
        if parsed.query or parsed.fragment or ".." in route.split("/") or "\\" in route:
            return json_reply({"error": "Unsupported request path"}, 400)
        try:
            if method == "GET" and route == "/":
                template = (
                    files("redteam_eval").joinpath("web", "index.html").read_text(encoding="utf-8")
                )
                return HTTPReply(
                    status=200,
                    body=template.replace(
                        "__CSRF_TOKEN__", html.escape(self.csrf, quote=True)
                    ).encode(),
                    content_type="text/html; charset=utf-8",
                )
            if method == "GET" and route.startswith("/assets/"):
                name = route.removeprefix("/assets/")
                if name not in {
                    "dashboard.css",
                    "dashboard.mjs",
                    "dashboard-core.mjs",
                    "suite-workbench.mjs",
                    "queue-workbench.mjs",
                    "results-organizer.mjs",
                    "model-workspace.mjs",
                    "project-workspace.mjs",
                }:
                    return json_reply({"error": "Asset not found"}, 404)
                content_type = (
                    "text/css; charset=utf-8"
                    if name.endswith(".css")
                    else "text/javascript; charset=utf-8"
                )
                return HTTPReply(
                    status=200,
                    body=files("redteam_eval").joinpath("web", name).read_bytes(),
                    content_type=content_type,
                )
            if method == "GET" and route == "/api/catalog":
                return json_reply(self.store.catalog())
            if method == "GET" and route == "/api/models":
                try:
                    return json_reply(asyncio.run(self.jobs.models()))
                except Exception as exc:
                    return json_reply(
                        {"ready": False, "models": [], "error": f"Local engine unavailable: {exc}"}
                    )
            if method == "GET" and route == "/api/jobs":
                return json_reply({"jobs": self.jobs.snapshots()})
            parts = route.strip("/").split("/")
            if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "runs"]:
                return json_reply(self.store.detail(parts[2]))
            if (
                method == "GET"
                and len(parts) == 4
                and parts[:2] == ["api", "runs"]
                and parts[3] == "scorecard"
            ):
                return json_reply(self.store.scorecard(parts[2]))
            if (
                method == "GET"
                and len(parts) == 5
                and parts[:2] == ["api", "runs"]
                and parts[3] == "cases"
            ):
                archive = self.store.archive(parts[2])
                pair = next((item for item in archive.results if item.key == parts[4]), None)
                if pair is None:
                    raise KeyError(parts[4])
                return json_reply(inspect_pair(pair))
            if (
                method == "GET"
                and len(parts) == 5
                and parts[:2] == ["api", "runs"]
                and parts[3] == "download"
            ):
                filename = parts[4]
                return HTTPReply(
                    status=200,
                    body=self.store.download(parts[2], filename),
                    content_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            if method == "GET" and route == "/api/projects":
                return json_reply(self.projects.read())
            if method == "GET" and route == "/api/job-details":
                return json_reply(self.job_details.read())
            if method == "GET" and route == "/api/queue":
                return json_reply(self.queue.snapshot())
            if method == "GET" and route == "/api/test-suites":
                return json_reply(self.suites.read())
            if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                return json_reply(self.jobs.snapshot(parts[2]))
            if method == "GET" and len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                if parts[3] == "notes":
                    return json_reply(self.jobs.notes(parts[2]))
                if parts[3] == "performance":
                    return json_reply(self.jobs.performance(parts[2]))
                if parts[3] == "report.html":
                    return HTTPReply(
                        status=200,
                        body=self.jobs.html_report(parts[2]).encode("utf-8"),
                        content_type="text/html; charset=utf-8",
                        headers={
                            "Content-Disposition": f'attachment; filename="skills-{parts[2]}.html"'
                        },
                    )
            if method == "POST":
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("JSON request must be an object")
                if route == "/api/projects":
                    project_request = ProjectSaveRequest.model_validate(payload)
                    existing: dict[str, Any] = next(
                        (
                            row
                            for row in self.projects.read()["projects"]
                            if row["id"] == project_request.id
                        ),
                        {},
                    )
                    available_suites = {row["id"] for row in self.suites.read()["suites"]}
                    if any(
                        identifier not in available_suites
                        and identifier not in existing.get("suite_ids", [])
                        for identifier in project_request.suite_ids
                    ):
                        raise ValueError("New suite links must refer to existing saved suites")
                    if any(
                        identifier not in self.jobs.jobs
                        and identifier not in existing.get("job_ids", [])
                        for identifier in project_request.job_ids
                    ):
                        raise ValueError(
                            "New job links must refer to jobs loaded in this dashboard"
                        )
                    return json_reply(self.projects.save(project_request))
                if route == "/api/project-queue":
                    launch = ProjectLaunchRequest.model_validate(payload)
                    project = next(
                        (row for row in self.projects.read()["projects"] if row["id"] == launch.id),
                        None,
                    )
                    if project is None or project["archived"]:
                        raise ValueError("Choose an existing unarchived project")
                    if project["revision"] != launch.revision:
                        raise RuntimeError("Project changed; refresh it before queuing")
                    if not project["suite_ids"] or set(launch.suite_revisions) != set(
                        project["suite_ids"]
                    ):
                        raise ValueError("Refresh the linked suite revisions before launching")
                    suites = {row["id"]: row for row in self.suites.read()["suites"]}
                    definitions = []
                    for identifier in project["suite_ids"]:
                        suite = suites.get(identifier)
                        if suite is None or suite["archived"]:
                            raise ValueError("Linked suites must exist and be unarchived")
                        if launch.suite_revisions[identifier] != suite["revision"]:
                            raise RuntimeError(
                                "A linked suite changed; refresh source links before queuing"
                            )
                        definitions.append(
                            {
                                "title": (project["title"] + " / " + suite["title"])[:100],
                                "settings": suite["settings"],
                                "suite_id": identifier,
                                "suite_revision": suite["revision"],
                                "project_id": project["id"],
                                "project_title": project["title"],
                                "project_revision": project["revision"],
                            }
                        )
                    return json_reply(self.queue.add(definitions, launch.queue_revision))
                if route == "/api/model-info":
                    model_request = ModelSelection.model_validate(payload)
                    return json_reply(asyncio.run(self.jobs.inspect_model(model_request.model)))
                if route == "/api/job-details":
                    details_request = DetailsRequest.model_validate(payload)
                    if details_request.job_id not in self.jobs.jobs:
                        raise ValueError("Choose a job loaded in the saved history")
                    return json_reply(self.job_details.save(details_request))
                if route == "/api/job-reference":
                    reference_request = ReferenceRequest.model_validate(payload)
                    if reference_request.job_id is not None:
                        reference_job = self.jobs.jobs.get(reference_request.job_id)
                        if (
                            reference_job is None
                            or reference_job.action != "check_model"
                            or reference_job.status not in ("complete", "failed", "cancelled")
                            or not (reference_job.result or {}).get("results")
                        ):
                            raise ValueError(
                                "Choose a stopped model-skill job with actual saved replies as the reference"
                            )
                    return json_reply(self.job_details.reference(reference_request))
                if route == "/api/queue/add":
                    addition = QueueAdd.model_validate(payload)
                    return json_reply(
                        self.queue.add(
                            [
                                {
                                    "title": addition.title,
                                    "settings": addition.settings.model_dump(mode="json"),
                                }
                            ],
                            addition.expected_revision,
                        )
                    )
                if route == "/api/queue/action":
                    return json_reply(self.queue.action(QueueAction.model_validate(payload)))
                if route == "/api/queue/suites":
                    batch = QueueBatch.model_validate(payload)
                    if len({item.id for item in batch.suites}) != len(batch.suites):
                        raise ValueError("Choose distinct saved suites")
                    library = {row["id"]: row for row in self.suites.read()["suites"]}
                    definitions = []
                    for suite_selection in batch.suites:
                        suite = library.get(suite_selection.id)
                        if suite is None or suite["archived"]:
                            raise ValueError("Choose existing, unarchived suites")
                        if suite["revision"] != suite_selection.revision:
                            raise RuntimeError(
                                "A selected suite changed. Refresh the library before queuing it."
                            )
                        definitions.append(
                            {
                                "title": suite["title"],
                                "settings": suite["settings"],
                                "suite_id": suite["id"],
                                "suite_revision": suite["revision"],
                            }
                        )
                    return json_reply(self.queue.add(definitions, batch.expected_revision))
                if route == "/api/test-suites":
                    return json_reply(self.suites.save(SuiteSaveRequest.model_validate(payload)))
                if route == "/api/jobs":
                    return json_reply(self.jobs.submit(payload).model_dump(mode="json"), 202)
                if route == "/api/skill-audit":
                    if payload:
                        raise ValueError(
                            "Saved-answer audit uses the configured reports root and an empty JSON object"
                        )
                    return json_reply(audit_saved_skills(self.store.root))
                if route == "/api/skill-plan":
                    plan_request = ModelCheckRequest.model_validate(payload)
                    return json_reply(build_skill_plan(plan_request.model_dump(mode="json")))
                if route == "/api/skill-consistency":
                    selection = SkillConsistencyRequest.model_validate(payload)
                    return json_reply(self.jobs.consistency_checks(selection.job_ids))
                if route == "/api/skill-comparison":
                    comparison_request = SkillComparisonRequest.model_validate(payload)
                    return json_reply(
                        self.jobs.compare_checks(
                            comparison_request.previous, comparison_request.current
                        )
                    )
                if route == "/api/regrade":
                    request = GatePreviewRequest.model_validate(payload)
                    archive = self.store.archive(request.run_id)
                    summary = summarize(archive.results, archive.config.seed)
                    failures = gate(summary, request.thresholds)
                    checks = []
                    for metric, limit in (
                        ("asr", request.thresholds.max_asr),
                        ("frr", request.thresholds.max_frr),
                        ("truncation_rate", request.thresholds.max_truncation_rate),
                    ):
                        point = summary["hardened"][metric]
                        observed = (
                            point["upper" if request.thresholds.use_upper_ci else "rate"]
                            if point["trials"]
                            else None
                        )
                        checks.append(
                            {
                                "metric": metric,
                                "observed": observed,
                                "limit": limit,
                                "comparison": "maximum",
                                "passed": observed is not None and observed <= limit + 1e-12,
                                "trials": point["trials"],
                            }
                        )
                    delta = summary["defense_delta"][
                        "lower" if request.thresholds.use_upper_ci else "delta"
                    ]
                    checks.append(
                        {
                            "metric": "defense_delta",
                            "observed": delta,
                            "limit": request.thresholds.min_defense_delta,
                            "comparison": "minimum",
                            "passed": delta + 1e-12 >= request.thresholds.min_defense_delta,
                        }
                    )
                    return json_reply(
                        {
                            "source_run_id": request.run_id,
                            "saved_thresholds": archive.thresholds.model_dump(),
                            "proposed_thresholds": request.thresholds.model_dump(),
                            "saved_gate_failures": gate(summary, archive.thresholds),
                            "gate_failures": failures,
                            "passed": not failures,
                            "checks": checks,
                            "inference_requested": False,
                            "source_modified": False,
                            "interpretation": "Recomputed gate preview over saved measurements. Confidence-bound grading uses upper Wilson bounds and the lower defense-delta bound. The saved run and its verdicts are unchanged.",
                        }
                    )
                if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                    if parts[3] == "retry":
                        retry_request = SkillRetryRequest.model_validate(payload)
                        return json_reply(
                            self.jobs.retry_checks(parts[2], retry_request.model).model_dump(
                                mode="json"
                            ),
                            202,
                        )
                    if parts[3] == "notes":
                        return json_reply(
                            self.jobs.notes(parts[2], NoteRequest.model_validate(payload))
                        )
                    if payload:
                        raise ValueError(
                            "Repeat, cancel, resume and saved-check review requests must use an empty JSON object"
                        )
                    if parts[3] == "repeat":
                        return json_reply(
                            self.jobs.repeat_checks(parts[2]).model_dump(mode="json"), 202
                        )
                    if parts[3] == "cancel":
                        return json_reply(self.jobs.cancel(parts[2]).model_dump(mode="json"))
                    if parts[3] == "review":
                        return json_reply(self.jobs.review_checks(parts[2]))
                    if parts[3] == "resume":
                        return json_reply(self.jobs.resume(parts[2]).model_dump(mode="json"), 202)
                if route == "/api/compare":
                    if set(payload) != {"previous", "current"} or not all(
                        isinstance(v, str) for v in payload.values()
                    ):
                        raise ValueError("Choose exactly a previous and current measured run")
                    return json_reply(
                        compare_runs(
                            self.store.archive(payload["previous"]),
                            self.store.archive(payload["current"]),
                            RegressionLimits(),
                        )
                    )
            return json_reply({"error": "Dashboard resource not found"}, 404)
        except KeyError:
            return json_reply({"error": "Measured artifact or job not found"}, 404)
        except ValidationError as exc:
            issues = [
                ".".join(str(v) for v in issue["loc"]) + ": " + issue["msg"]
                for issue in exc.errors()
            ]
            return json_reply(
                {"error": "Invalid request or archive: " + "; ".join(issues[:6])}, 400
            )
        except (ValueError, OSError, UnicodeError) as exc:
            return json_reply({"error": str(exc)}, 400)
        except RuntimeError as exc:
            return json_reply({"error": str(exc)}, 409)
        except Exception:
            logging.getLogger(__name__).exception("Dashboard request failed")
            return json_reply(
                {"error": "Dashboard could not read this resource; check the local server log"}, 500
            )


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: DashboardApp, port: int):
        self.app = app
        super().__init__(("127.0.0.1", port), DashboardHandler)
        app.port = self.server_address[1]


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(20)

    def respond(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536 or self.headers.get("Transfer-Encoding"):
                reply = json_reply({"error": "Unsupported or oversized request body"}, 413)
            else:
                body = self.rfile.read(length) if length else b""
                reply = self.server.app.handle(
                    self.command, self.path, dict(self.headers.items()), body
                )
        except (ValueError, TimeoutError):
            reply = json_reply({"error": "Invalid or incomplete HTTP request"}, 400)
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.content_type)
        self.send_header("Content-Length", str(len(reply.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        for key, value in reply.headers.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(reply.body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    do_GET = respond
    do_POST = respond
    do_OPTIONS = respond
    do_PUT = respond
    do_DELETE = respond

    def log_message(self, format: str, *args: Any) -> None:
        # HTTP request lines never include model prompts or response bodies.
        return


def serve_dashboard(root: Path, backend: BackendConfig, port: int = 8765) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("Choose a dashboard port from 0 through 65535")
    app = DashboardApp(root, backend, port)
    server = DashboardServer(app, port)
    print(f"Dashboard: http://127.0.0.1:{app.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.queue.shutdown()
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Could not persist paused queue at shutdown")
        with app.jobs.lock:
            if app.jobs.active:
                app.jobs.cancel(app.jobs.active)
        server.server_close()
