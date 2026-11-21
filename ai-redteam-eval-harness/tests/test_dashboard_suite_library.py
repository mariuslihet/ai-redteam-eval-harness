"""Persistence and safety boundaries for reusable setups, without model responses."""

import json

import pytest

from redteam_eval.dashboard import DashboardApp
from redteam_eval.dashboard_jobs import ModelCheckRequest
from redteam_eval.models import BackendConfig
from redteam_eval.suite_library import SuiteLibrary, SuiteSaveRequest


def definition(**changes):
    return {
        "title": "Weekly orders",
        "tags": ["orders", "JSON"],
        "settings": ModelCheckRequest(
            action="check_model", model="installed", test_suite="data", difficulty="challenging"
        ).model_dump(mode="json"),
        **changes,
    }


def test_library_round_trip_archive_restore_and_independent_copy(tmp_path):
    library = SuiteLibrary(tmp_path)
    assert library.read()["suites"] == [] and not library.path().exists()
    saved = library.save(SuiteSaveRequest(**definition()))["suite"]
    assert saved["revision"] == 1
    assert SuiteLibrary(tmp_path).read()["suites"] == [saved]
    updated = library.save(
        SuiteSaveRequest(
            **definition(
                title="Updated", archived=True, favorite=True, id=saved["id"], expected_revision=1
            )
        )
    )["suite"]
    assert updated["revision"] == 2 and updated["archived"]
    assert updated["created_at"] == saved["created_at"]
    restored = library.save(SuiteSaveRequest(**definition(id=saved["id"], expected_revision=2)))[
        "suite"
    ]
    assert restored["revision"] == 3 and not restored["archived"]
    duplicate = library.save(SuiteSaveRequest(**definition(title="Independent copy")))["suite"]
    assert duplicate["id"] != saved["id"] and duplicate["revision"] == 1
    assert len(library.read()["suites"]) == 2


def test_stale_edits_cannot_overwrite_saved_suite(tmp_path):
    library = SuiteLibrary(tmp_path)
    saved = library.save(SuiteSaveRequest(**definition()))["suite"]
    before = library.path().read_bytes()
    with pytest.raises(RuntimeError, match="another window"):
        library.save(SuiteSaveRequest(**definition(id=saved["id"], expected_revision=0)))
    assert library.path().read_bytes() == before


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"tags": ["Orders", " orders "]},
        {"tags": ["x" * 25]},
        {"tags": [""]},
        {"title": "x" * 81},
    ],
)
def test_invalid_metadata_is_rejected(changes):
    with pytest.raises(ValueError):
        SuiteSaveRequest(**definition(**changes))


def test_custom_settings_are_validated_and_retry_relationship_cannot_be_forged(tmp_path):
    settings = ModelCheckRequest(
        action="check_model",
        model="installed",
        test_suite="custom",
        custom_cases=[
            {
                "name": "Total",
                "prompt": "Count 6 boxes of 7 items.",
                "answer_type": "integer",
                "expected": "42",
            }
        ],
    ).model_dump(mode="json")
    saved = SuiteLibrary(tmp_path).save(SuiteSaveRequest(**definition(settings=settings)))["suite"]
    assert saved["settings"] == settings
    assert "results" not in saved and "response" not in saved
    settings["custom_cases"][0]["expected"] = "not an integer"
    with pytest.raises(ValueError):
        SuiteSaveRequest(**definition(settings=settings))
    settings = definition()["settings"] | {"retry_of": "a" * 32, "question_ids": ["data-3-0"]}
    with pytest.raises(ValueError):
        SuiteSaveRequest(**definition(settings=settings))


@pytest.mark.parametrize(
    "content", ["{", '{"format_version":1,"suites":null}', "[" * 257 + "0" + "]" * 257]
)
def test_corrupt_library_is_not_overwritten(tmp_path, content):
    library = SuiteLibrary(tmp_path)
    library.path().write_text(content, encoding="utf-8")
    before = library.path().read_bytes()
    with pytest.raises(ValueError):
        library.save(SuiteSaveRequest(**definition()))
    assert library.path().read_bytes() == before


def test_suite_api_persists_without_inference_and_requires_session(tmp_path):
    app = DashboardApp(tmp_path, BackendConfig())
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    payload = json.dumps(definition()).encode()
    assert (
        app.handle(
            "POST", "/api/test-suites", headers | {"X-Dashboard-Token": "wrong"}, payload
        ).status
        == 403
    )
    reply = app.handle("POST", "/api/test-suites", headers, payload)
    assert reply.status == 200 and not json.loads(reply.body)["inference_requested"]
    stored = json.loads(app.handle("GET", "/api/test-suites", headers).body)
    assert len(stored["suites"]) == 1
    saved = stored["suites"][0]
    stale = json.dumps(definition(id=saved["id"], expected_revision=0)).encode()
    assert app.handle("POST", "/api/test-suites", headers, stale).status == 409
    assert not app.jobs.jobs and app.jobs.active is None
    assert app.handle("GET", "/assets/suite-workbench.mjs", headers).status == 200
