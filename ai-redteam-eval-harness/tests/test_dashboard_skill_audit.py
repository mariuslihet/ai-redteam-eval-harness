"""Audits genuine recorded replies; synthetic values below are checker probes only."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from redteam_eval.cli import main
from redteam_eval.dashboard import DashboardApp
from redteam_eval.model_checks import SkillQuestion, grade_skill
from redteam_eval.models import BackendConfig, Response
from redteam_eval.skill_audit import audit_saved_skills


@pytest.fixture
def record():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "measured-qwen-skills.json").read_text(
            encoding="utf-8"
        )
    )


def question(expected):
    return SkillQuestion(
        id="checker-boundary",
        skill="JSON output",
        prompt="Validator input only",
        expected=expected,
        answer_type="json",
        rule="Exact JSON value",
    )


@pytest.mark.parametrize(
    "expected,reply", [([{"x": 1}], "[1]"), ([[1]], "[2]"), ([True], "[1]"), (["one"], "[1]")]
)
def test_valid_json_wrong_shape_is_not_a_format_error(expected, reply):
    grade = grade_skill(question(expected), Response(text=reply, finish_reason="stop"))
    assert grade["status"] == "wrong_answer" and grade["format_correct"] is True
    assert grade["answer_correct"] is False and not grade["passed"]
    assert not any("unhashable" in problem for problem in grade["problems"])


@pytest.mark.parametrize("text", ['{"x":1e-9999}', None])
def test_numeric_underflow_and_nesting_limits_need_review(text):
    if text is None:
        depth = sys.getrecursionlimit() + 100
        text = "[" * depth + "0" + "]" * depth
    grade = grade_skill(question({"x": 0}), Response(text=text, finish_reason="stop"))
    assert grade["status"] == "needs_review" and grade["format_correct"] is None
    json.dumps(grade, allow_nan=False)


def test_valid_integer_beyond_python_limit_is_not_blamed_on_model():
    limit = sys.get_int_max_str_digits()
    if not limit:
        pytest.skip("Interpreter integer limit is disabled")
    grade = grade_skill(question(0), Response(text="9" * (limit + 100), finish_reason="stop"))
    assert grade["status"] == "needs_review" and grade["format_correct"] is None


@pytest.mark.parametrize("text", ["{", "[1,]", '{"x":NaN}', '{"x":1,"x":1}'])
def test_real_format_errors_remain_format_errors(text):
    assert (
        grade_skill(question({"x": 1}), Response(text=text, finish_reason="stop"))["status"]
        == "format_error"
    )


def write_record(root, name, record):
    path = root / name / "model-checks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_audit_uses_native_records_skips_copies_and_preserves_sources(tmp_path, record):
    record["results"][0]["grade"]["passed"] = False
    record["results"].reverse()
    path = write_record(tmp_path, "first", record)
    copy_path = tmp_path / "copy" / "model-checks.json"
    copy_path.parent.mkdir()
    copy_path.write_bytes(path.read_bytes())
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = audit_saved_skills(tmp_path)
    assert result["files_audited"] == 1 and result["duplicate_files_skipped"] == 1
    assert result["audited_replies"] == 4 and result["counts"]["corrected_failures"] == 1
    assert sum(result["counts"].values()) == 4
    assert result["examples"][0]["category"] == "corrected_failures"
    assert not result["inference_requested"] and not result["source_modified"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert any(
        row["actual_reply"] == record["results"][0]["response"]["text"]
        for row in result["examples"]
    )


def test_uncertain_replies_and_missing_native_evidence_are_not_confirmed_failures(tmp_path, record):
    record["results"][1]["response"]["finish_reason"] = "length"
    record["results"][0]["response"]["response_envelope"] = {}
    write_record(tmp_path, "partial", record)
    result = audit_saved_skills(tmp_path)
    assert result["unverified_replies"] == 1 and result["audited_replies"] == 3
    assert result["counts"]["unconfirmed_failures"] == 1


def test_audit_reports_bad_files_and_explicit_scope_limits(tmp_path, record):
    write_record(tmp_path, "a", record)
    write_record(tmp_path, "b", record)
    bad = tmp_path / "bad" / "model-checks.json"
    bad.parent.mkdir()
    bad.write_text("{", encoding="utf-8")
    assert audit_saved_skills(tmp_path)["issues"]
    limited = audit_saved_skills(tmp_path, max_files=1)
    assert limited["truncated"] and limited["files_considered"] == 1
    empty = audit_saved_skills(tmp_path, max_replies=1)
    assert empty["truncated"] and empty["audited_replies"] == 0


def test_cli_and_protected_api_do_not_start_inference(tmp_path, record, capsys):
    write_record(tmp_path, "saved", record)
    assert main(["--audit-skills", "--reports-root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["audited_replies"] == 4
    app = DashboardApp(tmp_path, BackendConfig())
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    assert app.handle("POST", "/api/skill-audit", headers, b"{}").status == 200
    assert app.handle("POST", "/api/skill-audit", headers, b'{"root":"elsewhere"}').status == 400
    assert (
        app.handle("POST", "/api/skill-audit", headers | {"X-Dashboard-Token": "bad"}, b"{}").status
        == 403
    )
    assert app.jobs.active is None


def test_nesting_characters_inside_strings_do_not_trigger_checker_limit():
    value = {"x": "[" * 500 + chr(34) + chr(92) + "]" * 500}
    text = json.dumps(value)
    assert grade_skill(question(value), Response(text=text, finish_reason="stop"))["passed"]


def test_duplicate_question_ids_are_input_issues_not_extra_model_observations(tmp_path, record):
    import copy

    record["results"].append(copy.deepcopy(record["results"][0]))
    write_record(tmp_path, "duplicate-ids", record)
    result = audit_saved_skills(tmp_path)
    assert result["audited_replies"] == 0
    assert "Duplicate question IDs" in result["issues"][0]["error"]


@pytest.mark.parametrize("change", ["wrapper", "question"])
def test_changed_displayed_evidence_is_not_attributed_to_native_model(tmp_path, record, change):
    if change == "wrapper":
        row = record["results"][0]["response"]
        row["raw_text"] = row["text"]
        row["text"] = "Output withheld by a wrapper"
    else:
        record["results"][0]["question"]["prompt"] += " changed task"
    write_record(tmp_path, "changed-evidence", record)
    result = audit_saved_skills(tmp_path)
    assert result["unverified_replies"] == 1
    assert result["audited_replies"] == 3


def test_outer_report_nesting_is_bounded_before_decoding(tmp_path, monkeypatch):
    import redteam_eval.skill_audit as audit_module

    path = tmp_path / "deep" / "model-checks.json"
    path.parent.mkdir()
    path.write_text("[" * 500 + "0" + "]" * 500, encoding="utf-8")

    def must_not_decode(*args, **kwargs):
        raise AssertionError("Overdeep metadata must be stopped before decoding")

    monkeypatch.setattr(audit_module.json, "loads", must_not_decode)
    result = audit_saved_skills(tmp_path)
    assert result["audited_replies"] == 0
    assert "nesting" in result["issues"][0]["error"]
