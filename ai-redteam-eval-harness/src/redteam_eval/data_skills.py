"""Seeded data-reasoning questions with exact, locally computed answers."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from redteam_eval.model_checks import DATA_SKILLS, SkillQuestion, skill_names
from redteam_eval.models import derive_seed

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def data_questions(
    seed: int, count: int, selected_skills: list[str] | None, difficulty: str
) -> list[SkillQuestion]:
    questions = []
    challenging = difficulty == "challenging"
    for skill in skill_names("data", selected_skills):
        for index in range(count):
            rng = random.Random(derive_seed(seed, "data-skills-v1", difficulty, skill, index))
            rows: list[dict[str, Any]]
            if skill == "Working with dates":
                start = date(2024, 1, 1) + timedelta(days=rng.randrange(1460))
                days = rng.randrange(8, 26) if challenging else rng.randrange(3, 13)
                holidays = (
                    sorted({start + timedelta(days=rng.randrange(-3, 40)) for _ in range(6)})
                    if challenging
                    else []
                )
                due, remaining = start, days
                while remaining:
                    due += timedelta(days=1)
                    if due.weekday() < 5 and due not in holidays:
                        remaining -= 1
                data = {
                    "start_date": start.isoformat(),
                    "working_days": days,
                    "holidays": [value.isoformat() for value in holidays],
                }
                expected: Any = {"due_date": due.isoformat(), "weekday": WEEKDAYS[due.weekday()]}
                prompt = f"Schedule: {json.dumps(data)}. The start date is day zero and is NOT counted. Advance exactly working_days working days. Working days are Monday through Friday, excluding listed holidays. Skip Saturday and Sunday; a holiday on a weekend does not remove another day. Use Gregorian calendar dates with no time zones. Return only JSON with due_date in YYYY-MM-DD format and weekday as the full English weekday name."
                rule = "Advance after day zero, excluding weekends and listed dates without double-counting weekend holidays."
            elif skill == "Converting measurements":
                scales = {"mm": Decimal(1), "cm": Decimal(10), "m": Decimal(1000)}
                items: list[dict[str, Any]] = []
                if challenging:
                    items.append({"amount": "15", "unit": "m", "include": True})
                for n in range(8 if challenging else 4):
                    unit = rng.choice(list(scales))
                    millimeters = rng.randrange(10, 1201)
                    if challenging and n % 3 == 0:
                        millimeters *= -1
                    items.append(
                        {
                            "amount": str(Decimal(millimeters) / scales[unit]),
                            "unit": unit,
                            "include": not challenging or n % 4 != 2,
                        }
                    )
                total = int(
                    sum(
                        (
                            Decimal(row["amount"]) * scales[row["unit"]]
                            for row in items
                            if row["include"]
                        ),
                        Decimal(0),
                    )
                )
                expected = {"total_mm": total}
                if challenging:
                    expected.update(full_meters=total // 1000, remainder_mm=total % 1000)
                prompt = f"Measurements: {json.dumps(items)}. Use only rows where include is true. Amount strings are exact decimal numbers; negative amounts subtract. Convert using 1 m = 1000 mm and 1 cm = 10 mm. All converted amounts are whole millimeters and the final total is positive; no rounding is needed. Return only JSON with integer total_mm."
                if challenging:
                    prompt += " Also include integer full_meters and remainder_mm: divide total_mm into whole 1000-mm meters and a remainder from 0 through 999."
                rule = "Filter excluded rows, convert exact signed decimal amounts, sum millimeters and apply the requested quotient/remainder fields."
            elif skill == "Applying decision rules":
                rows = [
                    {
                        "id": f"CASE-{n}",
                        "active": rng.choice([True, False]),
                        "blocked": rng.choice([True, False]),
                        "owner_approved": rng.choice([True, False]),
                        "age_days": rng.randrange(1, 16),
                        "admin_override": rng.choice([True, False]),
                    }
                    for n in range(8 if challenging else 5)
                ]
                rows[0].update(
                    active=True,
                    blocked=False,
                    owner_approved=True,
                    age_days=9,
                    admin_override=False,
                )
                rows[1].update(
                    active=True, blocked=True, owner_approved=False, age_days=1, admin_override=True
                )
                allowed = [
                    row["id"]
                    for row in rows
                    if row["active"]
                    and not row["blocked"]
                    and (
                        (challenging and row["admin_override"])
                        or (row["owner_approved"] and row["age_days"] >= 7)
                    )
                ]
                expected = {
                    "allowed": sorted(allowed),
                    "denied": sorted(row["id"] for row in rows if row["id"] not in allowed),
                }
                rng.shuffle(rows)
                prompt = f"Records: {json.dumps(rows)}. Allow a record only if active is true, blocked is false, owner_approved is true and age_days is at least 7."
                prompt += (
                    " An admin_override of true waives ONLY owner approval and minimum age; it NEVER waives active or blocked checks."
                    if challenging
                    else " The admin_override field has no effect in this test."
                )
                prompt += " Return only JSON with allowed and denied arrays containing every record ID exactly once across both arrays. Sort each array alphabetically."
                rule = "Apply every mandatory condition, respect the precise scope of any override and partition all IDs without omissions or duplicates."
            else:
                initial = {"A": rng.randrange(50, 151), "B": rng.randrange(50, 151)}
                rows = []
                latest: dict[str, dict[str, Any]] = {}
                for n in range(6):
                    for revision in range(1, 3 if challenging else 2):
                        row: dict[str, Any] = {
                            "id": f"E{n}",
                            "revision": revision,
                            "counter": rng.choice(["A", "B"]),
                            "change": rng.randrange(-40, 61),
                            "voided": challenging and revision == 2 and n % 3 == 0,
                        }
                        rows.append(row)
                        latest[row["id"]] = row
                rows.extend([dict(rows[1]), dict(rows[-1])])
                rng.shuffle(rows)
                active = [row for row in latest.values() if not row["voided"]]
                totals = {
                    counter: initial[counter]
                    + sum(row["change"] for row in active if row["counter"] == counter)
                    for counter in initial
                }
                expected = {"totals": totals, "active_events": len(active)}
                prompt = f"Initial counters: {json.dumps(initial)}. Event rows: {json.dumps(rows)}. For each event id use ONLY its highest revision. Exact duplicate rows at the same id/revision count once. Then discard voided events; do not fall back to older revisions of a voided event. Add each remaining signed change to its named counter. Return only JSON with totals mapping A and B to their final integer values and active_events counting unique non-voided selected events."
                rule = "Resolve event identity and revision before void filtering; apply each surviving signed change exactly once to the correct counter."
            questions.append(
                SkillQuestion(
                    id=f"data-{difficulty}-{DATA_SKILLS.index(skill)}-{index}",
                    skill=skill,
                    prompt=prompt,
                    expected=expected,
                    answer_type="json",
                    rule=rule,
                )
            )
    return questions
