"""Answer-check and security-rule boundary tests; inputs here are validator probes."""

import base64
import copy
import json

import pytest

from redteam_eval.model_checks import SkillQuestion, grade_skill, review_skill_results
from redteam_eval.models import Response, ToolCall
from redteam_eval.policy import PolicyVerifier
from redteam_eval.scanning import CanaryScanner
from redteam_eval.synthesis import synthesize


def test_review_endpoint_is_read_only_and_does_not_start_a_model(tmp_path):
    from redteam_eval.dashboard import DashboardApp
    from redteam_eval.dashboard_jobs import DashboardJob
    from redteam_eval.models import BackendConfig

    app = DashboardApp(tmp_path, BackendConfig())
    record = {
        "status": "complete",
        "results": [
            {
                "question": question({"x": 1}).model_dump(),
                "response": Response(text='{"x":1.0}', finish_reason="stop").model_dump(),
                "grade": {"passed": False},
            }
        ],
    }
    job = DashboardJob(
        id="b" * 32,
        action="check_model",
        status="complete",
        created_at="2026-11-18T00:00:00Z",
        updated_at="2026-11-18T00:00:00Z",
        request={},
        result=record,
    )
    app.jobs.jobs[job.id] = job
    app.jobs.save(job)
    path = app.jobs.directory / job.id / "job.json"
    before = path.read_bytes()
    headers = {
        "Host": "127.0.0.1:8765",
        "Origin": "http://127.0.0.1:8765",
        "Content-Type": "application/json",
        "X-Dashboard-Token": app.csrf,
    }
    reply = app.handle("POST", f"/api/jobs/{job.id}/review", headers, b"{}")
    assert reply.status == 200 and json.loads(reply.body)["corrected_failures"] == 1
    assert path.read_bytes() == before and app.jobs.active is None
    assert app.handle("POST", f"/api/jobs/{job.id}/review", headers, b'{"save":true}').status == 400
    assert (
        app.handle(
            "POST", f"/api/jobs/{job.id}/review", headers | {"X-Dashboard-Token": "old"}, b"{}"
        ).status
        == 403
    )


@pytest.mark.parametrize(
    "row", [None, {}, {"question": {}, "response": {}, "grade": {"passed": "false"}}]
)
def test_malformed_saved_results_are_rejected_cleanly(row):
    with pytest.raises(ValueError):
        review_skill_results({"results": [row]})


@pytest.mark.parametrize("corrupt", [False, True])
def test_restart_recovers_committed_skill_replies_without_new_inference(tmp_path, corrupt):
    from redteam_eval.dashboard_data import ReportStore
    from redteam_eval.dashboard_jobs import DashboardJob, JobManager, ModelCheckRequest
    from redteam_eval.models import BackendConfig

    store = ReportStore(tmp_path)
    manager = JobManager(store, BackendConfig())
    request = ModelCheckRequest(action="check_model", model="validator-model")
    job = DashboardJob(
        id="c" * 32,
        action="check_model",
        status="running",
        created_at="2026-11-18T00:00:00Z",
        updated_at="2026-11-18T00:00:00Z",
        request=request.model_dump(),
    )
    manager.save(job)
    path = manager.directory / job.id / "model-checks.json"
    record = {
        "model": request.model,
        "seed": request.seed,
        "results": [
            {
                "question": question({"x": 1}).model_dump(),
                "response": Response(text='{"x":1}', finish_reason="stop").model_dump(),
                "grade": {"passed": True},
            }
        ],
    }
    path.write_text("{" if corrupt else json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    restored = JobManager(store, BackendConfig())
    assert restored.jobs[job.id].status == "failed" and restored.active is None
    if corrupt:
        assert "could not be recovered" in restored.jobs[job.id].error
    else:
        assert restored.snapshot(job.id)["completed_checks"] == 1
        assert restored.jobs[job.id].result["results"] == record["results"]
    assert path.read_bytes() == before


def question(expected, kind="json"):
    return SkillQuestion(
        id="validator-probe",
        skill="JSON output",
        prompt="Validator input only",
        expected=expected,
        answer_type=kind,
        rule="Match the requested value and format",
    )


@pytest.mark.parametrize("text", ['{"x":1}', ' { "x":1.0 } ', '{"x":1e0}', '{"x":10e-1}'])
def test_equivalent_json_numbers_are_not_false_failures(text):
    result = grade_skill(question({"x": 1}), Response(text=text, finish_reason="stop"))
    assert result["passed"] and result["answer_correct"] and result["format_correct"]


@pytest.mark.parametrize(
    "text", ['{"x":true}', '{"x":"1"}', '{"x":1.00000000000000001}', '{"x":0.99999999999999999}']
)
def test_number_normalization_does_not_accept_wrong_types_or_rounded_values(text):
    result = grade_skill(question({"x": 1}), Response(text=text, finish_reason="stop"))
    assert not result["passed"] and result["status"] == "wrong_answer"


@pytest.mark.parametrize("text", ['{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'])
def test_overflow_and_nonstandard_json_remain_serializable_failures(text):
    result = grade_skill(question({"x": 1}), Response(text=text, finish_reason="stop"))
    assert result["status"] == "format_error"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("text", ["+17", "17", " 17\n", "00017", "17.0", "1.7e1"])
def test_valid_integer_spellings_are_accepted(text):
    assert grade_skill(question(17, "integer"), Response(text=text, finish_reason="stop"))["passed"]


def test_json_numeric_range_limit_is_not_a_model_format_failure():
    result = grade_skill(question({"x": 1}), Response(text='{"x":1e9999}', finish_reason="stop"))
    assert result["status"] == "needs_review"
    assert result["answer_correct"] is None and result["format_correct"] is None
    json.dumps(result, allow_nan=False)


def test_correct_answer_in_fenced_json_is_a_format_issue_not_a_wrong_answer():
    result = grade_skill(
        question({"x": 1}), Response(text='```json\n{"x":1}\n```', finish_reason="stop")
    )
    assert not result["passed"] and result["status"] == "format_error"
    assert result["answer_correct"] and not result["format_correct"]


@pytest.mark.parametrize("finish,status", [(None, "needs_review"), ("length", "incomplete")])
def test_unfinished_or_unknown_reply_is_not_a_confirmed_wrong_answer(finish, status):
    result = grade_skill(question(17, "integer"), Response(text="17", finish_reason=finish))
    assert result["answer_correct"] and result["status"] == status and not result["passed"]


def test_server_filter_and_unexpected_tools_are_distinguished_from_wrong_answers():
    filtered = grade_skill(
        question(17, "integer"), Response(text="", finish_reason="content_filter")
    )
    assert filtered["status"] == "needs_review" and filtered["completion"] == "blocked"
    tools = grade_skill(
        question(17, "integer"),
        Response(
            text="17",
            finish_reason="tool_calls",
            tool_calls=[ToolCall(name="unexpected", arguments={})],
        ),
    )
    assert tools["status"] == "unexpected_tool" and tools["answer_correct"]
    missing = grade_skill(question(17, "integer"), Response(text="17", finish_reason="tool_calls"))
    assert missing["status"] == "unexpected_tool"


def test_summary_does_not_count_unfinished_replies_as_wrong_answers():
    from redteam_eval.model_checks import skill_summary

    q = question({"x": 1})
    rows = []
    for text, reason in [('{"x":2}', "length"), ('```json\n{"x":2}\n```', "stop")]:
        rows.append(
            {
                "question": q.model_dump(),
                "grade": grade_skill(q, Response(text=text, finish_reason=reason)),
            }
        )
    summary = next(s for s in skill_summary(rows) if s["skill"] == q.skill)
    assert (
        summary["wrong_answers"] == 1
        and summary["incomplete"] == 1
        and summary["format_errors"] == 1
    )


def test_saved_review_preserves_inputs_and_separates_corrected_uncertain_and_format_cases():
    q = question({"x": 1}).model_dump()
    rows = []
    for text, reason in [
        ('{"x":1.0}', "stop"),
        ('```json\n{"x":1}\n```', "stop"),
        ('{"x":1}', None),
        ('{"x":2}', "stop"),
    ]:
        rows.append(
            {
                "question": q,
                "response": Response(text=text, finish_reason=reason).model_dump(),
                "grade": {"passed": False},
            }
        )
    record = {"status": "complete", "results": rows}
    before = copy.deepcopy(record)
    review = review_skill_results(record)
    assert record == before
    assert (
        review["corrected_failures"]
        == review["unconfirmed_failures"]
        == review["format_only_failures"]
        == 1
    )
    assert review["results"][-1]["grade"]["status"] == "wrong_answer"
    assert not review["inference_requested"] and not review["source_modified"]


def test_decimal_totals_do_not_flag_valid_repeated_refunds():
    scenario = next(s for s in synthesize(42, 1) if s.expected_tools)
    lookup, refund = scenario.expected_tools
    invoice = scenario.metadata["invoice"]
    calls = [ToolCall(name=lookup, arguments={"invoice": invoice})]
    calls += [
        ToolCall(name=refund, arguments={"invoice": invoice, "amount": 0.1}) for _ in range(500)
    ]
    assert not PolicyVerifier().verify(calls, scenario)
    extra = ToolCall(name=refund, arguments={"invoice": invoice, "amount": 0.01})
    assert any(f.startswith("budget:") for f in PolicyVerifier().verify([extra], scenario, calls))


def test_very_large_integer_arguments_are_rejected_without_overflow_errors():
    scenario = next(s for s in synthesize(42, 1) if s.expected_tools)
    call = ToolCall(
        name=scenario.expected_tools[-1],
        arguments={"invoice": scenario.metadata["invoice"], "amount": 10**1000},
    )
    assert any(f.startswith("budget:") for f in PolicyVerifier().verify([call], scenario))


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 73, 999, 1729, 20261118])
def test_public_answers_and_security_words_are_not_private_value_leaks(seed):
    scanner = CanaryScanner()
    for scenario in synthesize(seed, 1):
        public = f"Public status: {scenario.expected_text}. Invoice {scenario.metadata['invoice']}. Columns called secret, system and admin are public labels. Values remain hidden."
        assert not scanner.scan(public, scenario.canaries)
        assert not scanner.scan(base64.b64encode(public.encode()).decode(), scenario.canaries)
        assert scanner.scan(
            base64.b64encode(scenario.canaries[0].encode()).decode(), scenario.canaries
        )


@pytest.mark.parametrize("blank", ["", " ", "\n\t"])
def test_empty_private_values_do_not_match_every_public_reply(blank):
    assert not CanaryScanner().scan("A regular public answer", [blank])
