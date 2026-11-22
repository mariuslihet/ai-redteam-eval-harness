"""Prompt validation and native conversation context; no substitute model replies."""

import copy
import json
from pathlib import Path

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.model_workspace import PromptRequest, prompt_messages, validate_prompt_record
from redteam_eval.models import BackendConfig


@pytest.mark.parametrize(
    "changes",
    [
        {"prompt": " "},
        {"prompt": "x" * 12001},
        {"system_prompt": "x" * 8001},
        {"max_tokens": 63},
        {"max_tokens": 4097},
        {"seed": -1},
        {"max_run_seconds": 181},
        {"max_total_tokens": 0},
        {"title": "\ud800"},
        {"expected_digest": "invalid"},
    ],
)
def test_invalid_prompt_settings_rejected(changes):
    values = {"model": "installed", "prompt": "A user task"} | changes
    with pytest.raises(ValueError):
        PromptRequest(**values)


def test_one_off_prompt_keeps_exact_instructions_and_does_not_add_schema():
    request = PromptRequest(
        model="installed",
        system_prompt="Exact system instructions",
        prompt=" Task with outer spaces ",
    )
    messages = prompt_messages(request)
    assert [row.model_dump() for row in messages] == [
        {"role": "system", "content": "Exact system instructions"},
        {"role": "user", "content": " Task with outer spaces "},
    ]
    assert (
        prompt_messages(PromptRequest(model="installed", system_prompt="", prompt="Task"))[0].role
        == "user"
    )
    with pytest.raises(ValueError, match="32,000"):
        prompt_messages(PromptRequest(model="installed", prompt="😀" * 9000))


@pytest.fixture
def native_parent():
    record = json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text()
    )
    row = record["results"][0]
    return {
        "model": record["model"],
        "model_digest": record["model_digest"],
        "messages": copy.deepcopy(row["response"]["request_envelope"]["body"]["messages"]),
        "settings": {
            "system_prompt": row["response"]["request_envelope"]["body"]["messages"][0]["content"]
        },
        "response": copy.deepcopy(row["response"]),
    }


def continuation(parent, **changes):
    return PromptRequest(
        **(
            {
                "model": parent["model"],
                "parent_job_id": "a" * 32,
                "system_prompt": parent["settings"]["system_prompt"],
                "prompt": "Follow-up task",
            }
            | changes
        )
    )


def test_continuation_uses_the_unchanged_actual_native_reply(native_parent):
    before = copy.deepcopy(native_parent)
    messages = prompt_messages(continuation(native_parent), native_parent)
    assert (
        messages[-2].role == "assistant"
        and messages[-2].content == native_parent["response"]["text"]
    )
    assert messages[-1].content == "Follow-up task"
    assert native_parent == before


@pytest.mark.parametrize(
    "change", ["reply", "context", "version", "model", "instructions", "cutoff"]
)
def test_changed_or_incomplete_context_is_rejected(native_parent, change):
    settings = {}
    if change == "reply":
        native_parent["response"]["text"] += " wrapper modification"
    elif change == "context":
        native_parent["messages"][0]["content"] = "Changed context"
    elif change == "version":
        native_parent["model_digest"] = "changed"
    elif change == "model":
        settings["model"] = "Other model"
    elif change == "instructions":
        settings["system_prompt"] = "Other instructions"
    else:
        native_parent["response"]["truncated"] = True
    with pytest.raises(ValueError):
        prompt_messages(continuation(native_parent, **settings), native_parent)


def test_prompt_api_requires_session_and_rejects_unknown_parent_before_starting(tmp_path):
    app = DashboardApp(tmp_path, BackendConfig())
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    payload = PromptRequest(model="installed", prompt="Task", parent_job_id="a" * 32).model_dump(
        mode="json"
    )
    assert (
        app.handle(
            "POST",
            "/api/jobs",
            headers | {"X-Dashboard-Token": "wrong"},
            json.dumps(payload).encode(),
        ).status
        == 403
    )
    assert app.handle("POST", "/api/jobs", headers, json.dumps(payload).encode()).status == 400
    assert not app.jobs.jobs and app.jobs.active is None
    assert app.handle("GET", "/assets/model-workspace.mjs", headers).status == 200


def test_recovery_checks_saved_native_evidence_without_changing_it(native_parent):
    settings = PromptRequest(
        model=native_parent["model"],
        system_prompt=native_parent["settings"]["system_prompt"],
        prompt=native_parent["messages"][-1]["content"],
    )
    record = {
        "format_version": 1,
        "kind": "prompt_reply",
        **copy.deepcopy(native_parent),
        "settings": settings.model_dump(mode="json"),
    }
    before = copy.deepcopy(record)
    assert validate_prompt_record(record, settings) == before
    record["response"]["text"] += " changed wrapper"
    with pytest.raises(ValueError, match="native evidence"):
        validate_prompt_record(record, settings)


def test_interrupted_prompt_recovers_committed_native_reply_without_inference(
    tmp_path, native_parent
):
    from redteam_eval.checkpoint import atomic_json
    from redteam_eval.dashboard_data import ReportStore
    from redteam_eval.dashboard_jobs import DashboardJob, JobManager, now

    settings = PromptRequest(
        model=native_parent["model"],
        system_prompt=native_parent["settings"]["system_prompt"],
        prompt=native_parent["messages"][-1]["content"],
    )
    manager = JobManager(ReportStore(tmp_path), BackendConfig())
    job = DashboardJob(
        id="a" * 32,
        action="prompt",
        status="running",
        created_at=now(),
        updated_at=now(),
        request=settings.model_dump(mode="json"),
    )
    manager.save(job)
    record = {
        "format_version": 1,
        "kind": "prompt_reply",
        **copy.deepcopy(native_parent),
        "settings": settings.model_dump(mode="json"),
    }
    path = manager.directory / job.id / "prompt-reply.json"
    atomic_json(path, record)
    original = path.read_bytes()
    restored = JobManager(ReportStore(tmp_path), BackendConfig())
    assert restored.jobs[job.id].status == "failed" and restored.jobs[job.id].result == record
    assert restored.active is None and path.read_bytes() == original
