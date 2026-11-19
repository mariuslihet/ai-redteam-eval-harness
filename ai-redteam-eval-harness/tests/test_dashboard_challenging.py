"""Independent task calculations and actual inference for challenging questions."""

import json
import os
import re
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from redteam_eval.dashboard_data import ReportStore
from redteam_eval.dashboard_jobs import JobManager, ModelCheckRequest
from redteam_eval.model_checks import grade_skill, skill_questions
from redteam_eval.models import BackendConfig, Response


def encoded_data(pattern, prompt):
    return json.loads(re.search(pattern, prompt)[1])


@pytest.mark.parametrize("seed", [0, 1, 42, 1729, 20261119])
def test_challenging_tasks_have_independently_verified_answers(seed):
    questions = skill_questions(seed, 3, "extended", difficulty="challenging")
    assert len(questions) == len({q.id for q in questions}) == 24
    assert questions == skill_questions(seed, 3, "extended", difficulty="challenging")
    assert all(
        q in questions for q in skill_questions(seed, 1, "extended", difficulty="challenging")
    )
    for q in questions:
        if q.skill == "Arithmetic":
            d = encoded_data(r"Inventory: (\{.*?\})\.", q.prompt)
            units = sum(
                [
                    d["boxes_first"] * d["units_first"],
                    d["boxes_second"] * d["units_second"],
                    d["returned_units"],
                    -d["damaged_units"],
                ]
            )
            assert q.expected == {
                "remaining_units": units,
                "full_packs": units // d["pack_size"],
                "loose_units": units % d["pack_size"],
            }
        elif q.skill == "Finding the right record":
            route = encoded_data(r"Route: (\{.*?\})\.", q.prompt)
            aliases = encoded_data(r"Aliases: (\[.*?\])\.", q.prompt)
            rows = encoded_data(r"Records: (\[.*?\])\.", q.prompt)
            target = next(row["target_id"] for row in aliases if row["alias"] == route["alias"])
            matching = [
                row
                for row in rows
                if (row["id"], row["revision"], row["state"])
                == (target, route["revision"], "active")
            ]
            assert len(matching) == 1 and matching[0]["value"] == q.expected
            assert any(
                row["id"] == target
                and row["revision"] == route["revision"]
                and row["state"] == "archived"
                for row in rows
            )
        elif q.skill == "JSON output":
            rows = encoded_data(r"Items: (\[.*?\])\.", q.prompt)
            active = [row for row in rows if row["cancelled"] is False]
            assert q.expected == {
                "reference": re.search(r"Reference: (CODE-\w+)", q.prompt)[1],
                "summary": {
                    "active_count": len(active),
                    "total": sum(
                        sum(row["unit_price"] for _ in range(row["quantity"])) for row in active
                    ),
                },
                "tags": sorted(set(row["tag"] for row in active)),
                "approved": False,
            }
        elif q.skill == "Following instructions":
            assert q.expected == re.search(r"required_reply is (CODE-\w+)", q.prompt)[1]
            assert q.prompt.count('"role"') == 3 and "WRONG-" + q.expected in q.prompt
        elif q.skill == "Sorting and removing duplicates":
            rows = encoded_data(r"Records: (\[.*?\])\.", q.prompt)
            latest = [
                max(
                    (row for row in rows if row["id"] == identifier),
                    key=lambda row: row["revision"],
                )
                for identifier in {row["id"] for row in rows}
            ]
            active = [row for row in latest if row["active"]]
            # Stable sorts in reverse priority, independent of generator's tuple key.
            active.sort(key=lambda row: row["id"])
            active.sort(key=lambda row: row["label"])
            active.sort(key=lambda row: row["score"], reverse=True)
            assert q.expected == [row["id"] for row in active]
            assert any(not row["active"] for row in latest)
        elif q.skill == "Combining order records":
            prices = encoded_data(r"cents\): (\{.*?\})\.", q.prompt)
            rows = encoded_data(r"Rows: (\[.*?\])\.", q.prompt)
            subtotal = sum(
                prices[row["product"]] * row["quantity"] for row in rows if not row["cancelled"]
            )
            discount = int(Decimal(subtotal) * Decimal("0.1")) if subtotal >= 5000 else 0
            taxed = subtotal - discount
            tax = int(
                (Decimal(taxed) * Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            shipping = 0 if taxed >= 7000 else 399
            assert q.expected == dict(
                subtotal_cents=subtotal,
                discount_cents=discount,
                tax_cents=tax,
                shipping_cents=shipping,
                total_cents=taxed + tax + shipping,
            )
        elif q.skill == "Planning dependent tasks":
            rows = encoded_data(r"Tasks: (\[.*?\])\.", q.prompt)
            mapping = {row["id"]: row for row in rows}

            def finish(identifier):
                row = mapping[identifier]
                return row["minutes"] + max((finish(parent) for parent in row["after"]), default=0)

            ends = {identifier: finish(identifier) for identifier in mapping}
            assert len(ends) == 10 and q.expected == {
                "finish_minutes": ends,
                "project_minutes": max(ends.values()),
            }
        else:
            docs = encoded_data(r"Documents: (\[.*?\])\. Use", q.prompt)
            included = [doc for doc in docs if doc["include"]]
            counts = {
                word: sum(
                    len(
                        re.findall(
                            r"(?:^| )[,.;:!?()\[\]\"]*" + word + r"[,.;:!?()\[\]\"]*(?= |$)",
                            doc["text"],
                            flags=re.IGNORECASE,
                        )
                    )
                    for doc in included
                )
                for word in ("red", "blue")
            }
            assert q.expected == {**counts, "included_documents": len(included)}
        # Checker inputs only; never used as backend replies or saved model results.
        text = json.dumps(q.expected) if q.answer_type == "json" else str(q.expected)
        assert grade_skill(q, Response(text=text, finish_reason="stop"))["passed"]
        assert not grade_skill(q, Response(text="wrong", finish_reason="stop"))["passed"]


def test_standard_tasks_and_focused_challenging_tasks_keep_stable_identities():
    assert skill_questions(42, 2) == skill_questions(42, 2, difficulty="standard")
    full = skill_questions(42, 3, "extended", difficulty="challenging")
    chosen = skill_questions(
        42, 3, "extended", ["Planning dependent tasks", "Arithmetic"], "challenging"
    )
    assert chosen == [q for q in full if q.skill in {"Arithmetic", "Planning dependent tasks"}]
    assert not {q.id for q in full} & {q.id for q in skill_questions(42, 3, "extended")}
    graphs = [
        next(
            q
            for q in skill_questions(seed, 1, "extended", difficulty="challenging")
            if q.skill == "Planning dependent tasks"
        ).prompt
        for seed in range(4)
    ]
    assert len(set(graphs)) == 4


def test_record_sorting_explanations_use_ranking_rules_not_numeric_order():
    q = next(
        q
        for q in skill_questions(42, 1, "extended", difficulty="challenging")
        if q.skill == "Sorting and removing duplicates"
    )
    wrong_order = list(reversed(q.expected))
    result = grade_skill(q, Response(text=json.dumps(wrong_order), finish_reason="stop"))
    assert "The IDs are not in the required score, label and ID order" in result["problems"]
    assert not any("increasing" in message for message in result["problems"])
    result = grade_skill(q, Response(text=json.dumps(q.expected + [1]), finish_reason="stop"))
    assert "IDs that should not appear: 1" in result["problems"]
    assert not any("not in the input" in message for message in result["problems"])


def test_generated_order_questions_cover_both_threshold_branches_and_half_cent_tax():
    answers = [
        skill_questions(seed, 1, "extended", ["Combining order records"], "challenging")[0].expected
        for seed in range(100)
    ]
    assert any(answer["discount_cents"] == 0 for answer in answers)
    assert any(answer["discount_cents"] > 0 for answer in answers)
    assert {answer["shipping_cents"] for answer in answers} == {0, 399}
    assert any(
        (answer["subtotal_cents"] - answer["discount_cents"]) % 20 == 10 for answer in answers
    )


@pytest.mark.parametrize("difficulty", ["hard", "easy", "", None])
def test_invalid_difficulty_is_rejected(difficulty):
    with pytest.raises(ValueError):
        ModelCheckRequest.model_validate(
            {"action": "check_model", "model": "installed", "difficulty": difficulty}
        )


@pytest.mark.local_llm
def test_actual_challenging_run_retains_native_answers_and_reloads(tmp_path):
    model = os.getenv("REDTEAM_LOCAL_MODEL")
    if not model:
        pytest.skip("Set REDTEAM_LOCAL_MODEL for actual installed inference")
    root = Path(os.getenv("REDTEAM_CHALLENGING_LIVE_ROOT", str(tmp_path)))
    manager = JobManager(ReportStore(root), BackendConfig(model=model))
    job = manager.submit(
        {
            "action": "check_model",
            "model": model,
            "test_suite": "extended",
            "difficulty": "challenging",
            "seed": 20261119,
        }
    )
    deadline = time.monotonic() + 300
    while manager.active is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    saved = manager.snapshot(job.id)
    assert saved["status"] == "complete", saved.get("error")
    assert saved["completed_checks"] == saved["total_checks"] == 8
    assert saved["result"]["settings"]["difficulty"] == "challenging"
    for item, q in zip(
        saved["result"]["results"],
        skill_questions(20261119, 1, "extended", difficulty="challenging"),
        strict=True,
    ):
        assert item["question"] == q.model_dump(mode="json")
        response = Response.model_validate(item["response"])
        assert response.response_envelope["model"] == model
        assert response.text == response.response_envelope["message"]["content"]
        assert response.model_digest == saved["result"]["model_digest"]
        assert response.usage.input_tokens > 0
        assert "format" not in response.request_envelope["body"]
        assert grade_skill(q, response) == item["grade"]
    assert len(manager.review_checks(job.id)["summary"]) == 8
    assert manager._skill_checkpoint(manager.jobs[job.id]) == saved["result"]
    reloaded = JobManager(ReportStore(root), BackendConfig(model=model))
    assert reloaded.snapshot(job.id)["result"] == saved["result"]
