"""Bounded paired experiments with incremental resume and adaptive baseline replay."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import httpx

from redteam_eval.attacker import RedAgent
from redteam_eval.backends import make_backend
from redteam_eval.checkpoint import CheckpointStore
from redteam_eval.defense import DefensePipeline, Guardrail, context_message
from redteam_eval.evaluation import HybridEvaluator
from redteam_eval.models import (
    Backend,
    Message,
    PairResult,
    ProviderProfile,
    Request,
    Response,
    RunConfig,
    Scenario,
    ToolCall,
    Trajectory,
    Turn,
    Usage,
    derive_seed,
)
from redteam_eval.mutations import Bandit, Mutation, PromptMutator
from redteam_eval.providers import ProviderSimulator, RateLimited
from redteam_eval.synthesis import synthesize

LOGGER = logging.getLogger(__name__)


class RetryingBackend:
    """Use the same transient-only retry policy for attacker and judge calls."""

    def __init__(self, backend: Backend, runner: Runner):
        self.backend, self.runner = backend, runner

    async def generate(self, request: Request) -> Response:
        return await self.runner.retry(lambda _: self.backend.generate(request), request.seed)


class Runner:
    def __init__(
        self,
        config: RunConfig,
        backend: Backend | None = None,
        attacker: Backend | None = None,
        judge: Backend | None = None,
        guardrail: Guardrail | None = None,
    ):
        self.config = config
        self.backend = backend or make_backend(config.backend)
        red = attacker or (make_backend(config.attacker) if config.attacker else None)
        semantic = judge or (make_backend(config.judge) if config.judge else None)
        self.red = RedAgent(RetryingBackend(red, self) if red else None)
        self.evaluator = HybridEvaluator(
            RetryingBackend(semantic, self) if semantic else None,
            config.deterministic_precedence,
            config.require_judge,
        )
        self.defense: Guardrail = guardrail or DefensePipeline(config.guard)
        self.mutator = PromptMutator()

    async def retry(self, operation: Callable[[int], Awaitable[Response]], seed: int) -> Response:
        start = time.perf_counter()
        for attempt in range(self.config.retries + 1):
            try:
                response = await operation(attempt)
                response.attempts = attempt + 1
                response.latency_ms = max(response.latency_ms, (time.perf_counter() - start) * 1000)
                return response
            except (RateLimited, httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    raise
                if attempt == self.config.retries:
                    raise
                jitter = random.Random(derive_seed(seed, "retry", attempt)).uniform(0.8, 1.2)
                delay = self.config.backoff_seconds * 2**attempt * jitter
                if isinstance(exc, RateLimited):
                    delay = max(delay, exc.retry_after)
                elif isinstance(exc, httpx.HTTPStatusError):
                    try:
                        delay = max(
                            delay, min(60, float(exc.response.headers.get("retry-after", "0")))
                        )
                    except ValueError:
                        pass
                LOGGER.info(json.dumps({"event": "retry", "attempt": attempt + 1, "delay": delay}))
                await asyncio.sleep(delay)
        raise RuntimeError("Unreachable retry state")

    async def run(self, checkpoint: Path | None = None) -> list[PairResult]:
        scenarios = synthesize(self.config.seed, self.config.cases_per_family)
        store = CheckpointStore(checkpoint, self.config, scenarios) if checkpoint else None
        semaphore = asyncio.Semaphore(self.config.concurrency)

        async def worker(scenario: Scenario, profile: ProviderProfile) -> PairResult:
            key = f"{scenario.id}:{profile.name}"
            if store:
                saved = store.load(key)
                if saved:
                    LOGGER.info(json.dumps({"event": "resumed", "key": key}))
                    return saved
            async with semaphore:
                result = await self.pair(scenario, profile, key)
                if store:
                    store.save(result)
                LOGGER.info(json.dumps({"event": "pair_complete", "key": key}))
                return result

        tasks: list[asyncio.Task[PairResult]] = []
        try:
            # TaskGroup cancels and joins siblings before the checkpoint lock is released.
            async with asyncio.TaskGroup() as group:
                for scenario in scenarios:
                    for profile in self.config.providers:
                        tasks.append(group.create_task(worker(scenario, profile)))
            return [task.result() for task in tasks]
        finally:
            if store:
                store.close()

    async def pair(self, scenario: Scenario, profile: ProviderProfile, key: str) -> PairResult:
        simulator = ProviderSimulator(self.backend, profile)
        plan: list[Mutation] = []
        bandit = Bandit(derive_seed(scenario.seed, profile.name, "bandit"))
        trajectories: list[Trajectory] = []
        for mode in ("baseline", "hardened"):
            history = [
                Message(role="system", content=scenario.system),
                context_message(scenario.document),
            ]
            turns: list[Turn] = []
            prior_calls: list[ToolCall] = []
            max_turns = 1 if scenario.benign else self.config.max_turns
            for depth in range(max_turns):
                turn_seed = derive_seed(scenario.seed, profile.name, depth)
                attacker_usage, attacker_latency = Usage(), 0.0
                if mode == "baseline":
                    if depth == 0:
                        mutation = Mutation(operator="identity", prompt=scenario.prompt)
                    else:
                        operator, score = bandit.select()
                        mutation = self.mutator.apply(
                            scenario.prompt + "\n" + scenario.document, turn_seed, operator
                        )
                        mutation.score = score
                        mutation, attacker_usage, attacker_latency = await self.red.refine(
                            mutation, turns[-1], derive_seed(turn_seed, "red")
                        )
                    plan.append(mutation)
                else:
                    if depth >= len(plan):
                        break
                    mutation = plan[depth]
                for segment in mutation.prelude:
                    history.append(Message(role="user", content=segment))
                history.append(Message(role="user", content=mutation.prompt))
                request = Request(
                    messages=history,
                    tools=scenario.tools,
                    seed=turn_seed,
                    max_tokens=self.config.max_tokens,
                )
                events: list[str] = []
                preparation_ms = 0.0
                if mode == "hardened":
                    start = time.perf_counter()
                    request, events = self.defense.prepare(request, scenario)
                    preparation_ms = (time.perf_counter() - start) * 1000
                response = await self.retry(
                    lambda attempt: simulator.generate(request, attempt), turn_seed
                )
                response.guard_events.extend(events)
                response.latency_ms += preparation_ms
                if mode == "hardened":
                    response = self.defense.enforce(response, scenario, prior_calls)
                verdict, judge_usage, judge_latency = await self.evaluator.evaluate(
                    response, scenario, derive_seed(turn_seed, "judge"), prior_calls
                )
                prior_calls.extend(response.tool_calls)
                state: Literal["searching", "success", "exhausted", "control_complete"] = (
                    "control_complete"
                    if scenario.benign
                    else "success"
                    if verdict.attack_success
                    else "exhausted"
                    if depth + 1 == max_turns
                    else "searching"
                )
                turn = Turn(
                    id=f"{key}:{mode}:{depth}",
                    parent_id=turns[-1].id if turns else None,
                    depth=depth + 1,
                    mutation=mutation.operator,
                    prompt="\n".join([*mutation.prelude, mutation.prompt]),
                    response=response,
                    verdict=verdict,
                    state=state,
                    attacker_usage=attacker_usage,
                    attacker_latency_ms=attacker_latency,
                    judge_usage=judge_usage,
                    judge_latency_ms=judge_latency,
                    strategy_score=mutation.score,
                    attacker_outputs=mutation.refinements,
                )
                turns.append(turn)
                if mode == "baseline" and mutation.operator != "identity":
                    bandit.update(
                        mutation.operator,
                        1.0 if verdict.attack_success else 0.25 if verdict.disagreement else 0.0,
                    )
                history.append(
                    Message(
                        role="assistant",
                        content=response.text
                        + (
                            "\nProposed tools: "
                            + json.dumps([c.model_dump() for c in response.tool_calls])
                            if response.tool_calls
                            else ""
                        ),
                    )
                )
                # Always finish replaying the baseline plan in hardened mode, even if a
                # hardened turn succeeds early; matched budgets prevent biased deltas.
                if mode == "baseline" and (verdict.attack_success or scenario.benign):
                    break
            if turns and turns[-1].state == "searching":
                turns[-1].state = "exhausted"
            trajectories.append(
                Trajectory(
                    scenario_id=scenario.id,
                    family=scenario.family,
                    benign=scenario.benign,
                    provider=profile.name,
                    mode=mode,
                    turns=turns,
                )
            )
        return PairResult(
            key=key, scenario=scenario, baseline=trajectories[0], hardened=trajectories[1]
        )
