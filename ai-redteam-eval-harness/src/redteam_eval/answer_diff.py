"""Bounded, descriptive differences for already-checked JSON answers."""

from __future__ import annotations

from typing import Any


def answer_differences(expected: Any, actual: Any, limit: int = 30) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Choose a difference limit from 1 to 100")
    changes: list[dict[str, Any]] = []

    def add(path: str, kind: str, before: Any, after: Any) -> None:
        if len(changes) < limit:
            changes.append({"path": path or "/", "kind": kind, "expected": before, "actual": after})

    def walk(before: Any, after: Any, path: str, depth: int) -> None:
        if len(changes) >= limit:
            return
        if depth > 12:
            add(path, "depth_limit", "Nested values omitted", "Inspect the original JSON")
        elif type(before) is not type(after):
            names = {
                dict: "object",
                list: "array",
                str: "text",
                int: "integer",
                float: "number",
                bool: "boolean",
                type(None): "null",
            }
            add(
                path,
                "type",
                names.get(type(before), "unsupported type"),
                names.get(type(after), "unsupported type"),
            )
        elif isinstance(before, dict):
            for key in sorted(before.keys() | after.keys(), key=str):
                if len(changes) >= limit:
                    break
                child = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
                if key not in after:
                    add(child, "missing_key", before[key], "Key missing")
                elif key not in before:
                    add(child, "extra_key", "Key not requested", after[key])
                else:
                    walk(before[key], after[key], child, depth + 1)
        elif isinstance(before, list):
            if len(before) != len(after):
                add(path, "array_length", len(before), len(after))
            for index, (old, new) in enumerate(zip(before, after)):
                if len(changes) >= limit:
                    break
                walk(old, new, path + "/" + str(index), depth + 1)
        elif before != after:
            add(path, "value", before, after)

    walk(expected, actual, "", 0)
    return changes
