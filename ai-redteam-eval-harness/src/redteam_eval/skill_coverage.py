"""Actual saved-question coverage, without extrapolating model ability."""

from __future__ import annotations

from typing import Any

from redteam_eval.model_checks import SkillQuestion, exact_value


def skill_coverage(planned: list[SkillQuestion], reviewed: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {q.id: q for q in planned}
    if len(expected) != len(planned):
        raise ValueError("Planned question IDs must be unique")
    seen: set[str] = set()
    valid = {}
    excluded = []
    for row in reviewed:
        identifier = row["question"]["id"]
        if identifier in seen:
            raise ValueError("Duplicate saved question IDs make coverage ambiguous")
        seen.add(identifier)
        if identifier not in expected or not exact_value(
            row["question"], expected[identifier].model_dump(mode="json")
        ):
            excluded.append(identifier)
        else:
            valid[identifier] = row

    def counts(questions: list[SkillQuestion]) -> dict[str, int]:
        grades = [valid[q.id]["grade"] for q in questions if q.id in valid]
        passed = sum(
            grade["status"] == "passed" and grade["completion"] == "complete" for grade in grades
        )
        failed = sum(
            grade["status"] in {"wrong_answer", "format_error", "unexpected_tool"}
            and grade["completion"] == "complete"
            for grade in grades
        )
        return {
            "planned": len(questions),
            "saved": len(grades),
            "passed": passed,
            "failed": failed,
            "unconfirmed": len(grades) - passed - failed,
            "unanswered": len(questions) - len(grades),
        }

    skills = list(dict.fromkeys(q.skill for q in planned))
    return {
        "overall": counts(planned),
        "skills": [
            {"skill": name, **counts([q for q in planned if q.skill == name])} for name in skills
        ],
        "excluded_question_ids": excluded,
        "inference_requested": False,
        "source_modified": False,
        "interpretation": "Coverage describes this exact plan, not general model capability. Passed and failed use the current checker. Unfinished or review-needed replies remain unconfirmed. Missing replies are unanswered, not failures. Saved questions that differ from the plan are excluded; original reports are unchanged.",
    }
