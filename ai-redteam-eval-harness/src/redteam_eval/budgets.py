"""Bounded execution using observed real-backend usage, with explicit resume accounting."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import Field

from redteam_eval.models import Model, PairResult, RunConfig, Usage


class BudgetExceeded(RuntimeError):
    """Execution stopped without substituting model responses or publishing partial metrics."""


class ExecutionRecord(Model):
    status: Literal["running", "complete", "failed", "budget_exceeded", "cancelled"] = "running"
    observed_tokens: int = Field(default=0, ge=0)
    restored_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    model_responses: int = Field(default=0, ge=0)
    estimated_responses: int = Field(default=0, ge=0)
    restored_pairs: int = Field(default=0, ge=0)
    request_attempts: int = Field(default=0, ge=0)
    failed_attempts: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)
    max_total_tokens: int | None = None
    max_run_seconds: float | None = None


def is_budget_error(error: BaseException) -> bool:
    if isinstance(error, BudgetExceeded):
        return True
    return isinstance(error, BaseExceptionGroup) and any(
        is_budget_error(e) for e in error.exceptions
    )


class RunBudget:
    def __init__(self, config: RunConfig):
        self.started = time.monotonic()
        self.finished: float | None = None
        self.record = ExecutionRecord(
            max_total_tokens=config.max_total_tokens, max_run_seconds=config.max_run_seconds
        )

    def before_request(self) -> None:
        cap = self.record.max_total_tokens
        if cap is not None and self.record.total_tokens >= cap:
            raise BudgetExceeded(
                f"Observed token allowance exhausted ({self.record.total_tokens}/{cap}); no new request was started"
            )
        self.record.request_attempts += 1

    def observe(self, usage: Usage) -> None:
        self.record.model_responses += 1
        self.record.observed_tokens += usage.input_tokens + usage.output_tokens
        self.record.total_tokens = self.record.observed_tokens + self.record.restored_tokens
        self.record.estimated_responses += int(usage.estimated)
        self.check(usage.estimated)

    def restore(self, pair: PairResult) -> None:
        usages = [
            usage
            for trace in (pair.baseline, pair.hardened)
            for turn in trace.turns
            for usage in (turn.response.usage, turn.attacker_usage, turn.judge_usage)
        ]
        self.record.restored_pairs += 1
        self.record.restored_tokens += sum(u.input_tokens + u.output_tokens for u in usages)
        self.record.total_tokens = self.record.observed_tokens + self.record.restored_tokens
        self.check(any(u.estimated for u in usages))

    def check(self, estimated: bool) -> None:
        cap = self.record.max_total_tokens
        if cap is None:
            return
        if estimated:
            raise BudgetExceeded(
                "Cannot enforce an exact observed-token budget with estimated or missing backend usage metadata"
            )
        if self.record.total_tokens > cap:
            raise BudgetExceeded(
                f"Observed token budget exceeded ({self.record.total_tokens}/{cap}); further requests were stopped"
            )

    def snapshot(self) -> ExecutionRecord:
        self.record.elapsed_seconds = (
            self.finished if self.finished is not None else time.monotonic()
        ) - self.started
        return self.record.model_copy(deep=True)

    def finish(self, status: Literal["complete", "failed", "budget_exceeded", "cancelled"]) -> None:
        self.record.status = status
        self.finished = time.monotonic()
