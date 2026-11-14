"""Atomic per-pair checkpoints, manifest compatibility and exclusive run ownership."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from redteam_eval import __version__
from redteam_eval.models import PairResult, RunConfig, Scenario


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class CheckpointStore:
    """A pair commits only after both modes complete; interruption reruns only that pair."""

    def __init__(self, directory: Path, config: RunConfig, scenarios: list[Scenario]):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = directory / ".run.lock"
        try:
            with self.lock.open("x", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
        except FileExistsError as exc:
            raise ValueError(
                "Checkpoint is locked; after a crashed process, verify it stopped "
                "before removing .run.lock"
            ) from exc
        try:
            payload = {
                "format_version": 1,
                "package_version": __version__,
                "config": config.model_dump(mode="json"),
                "scenarios": [s.model_dump(mode="json") for s in scenarios],
            }
            self.fingerprint = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest()
            manifest = directory / "manifest.json"
            if manifest.exists():
                existing = json.loads(manifest.read_text(encoding="utf-8"))
                if existing["fingerprint"] != self.fingerprint:
                    raise ValueError(
                        "Checkpoint configuration or generated suite differs; use a new directory"
                    )
            else:
                atomic_json(manifest, {"fingerprint": self.fingerprint, **payload})
        except BaseException:
            self.close()
            raise

    def path(self, key: str) -> Path:
        return self.directory / f"pair-{hashlib.sha256(key.encode()).hexdigest()}.json"

    def load(self, key: str) -> PairResult | None:
        path = self.path(key)
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        canonical = json.dumps(record["result"], sort_keys=True).encode()
        if hashlib.sha256(canonical).hexdigest() != record["sha256"]:
            raise ValueError(f"Corrupt checkpoint: {path.name}")
        pair = PairResult.model_validate(record["result"])
        if pair.key != key:
            raise ValueError("Checkpoint key mismatch")
        return pair

    def save(self, result: PairResult) -> None:
        value = result.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        atomic_json(self.path(result.key), {"result": value, "sha256": digest})

    def close(self) -> None:
        self.lock.unlink(missing_ok=True)
