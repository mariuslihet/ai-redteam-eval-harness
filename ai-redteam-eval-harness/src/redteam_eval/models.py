"""Validated domain contracts shared by inference, experiments and persisted results."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Family(StrEnum):
    INJECTION = "indirect_injection"
    LEAKAGE = "sensitive_leakage"
    AGENCY = "excessive_agency"


class Risk(StrEnum):
    READ_ONLY = "READ_ONLY"
    STATE_MUTATING = "STATE_MUTATING"
    PRIVILEGED_ADMIN = "PRIVILEGED_ADMIN"


class Outcome(StrEnum):
    FULL = "FULL_COMPLIANCE_WITH_ATTACK"
    PARTIAL = "PARTIAL_LEAK_OR_POLICY_DRIFT"
    SAFE = "SAFE_REFUSAL"
    FALSE_REFUSAL = "FALSE_REFUSAL_ON_BENIGN"
    BENIGN = "BENIGN_COMPLIANCE"


class Message(Model):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ToolSpec(Model):
    name: str
    description: str
    risk: Risk
    parameters: dict[str, Any]
    requires: list[str] = Field(default_factory=list)
    path_roots: dict[str, str] = Field(default_factory=dict)
    recipient_domains: dict[str, str] = Field(default_factory=dict)
    cumulative_limits: dict[str, float] = Field(default_factory=dict)


class ToolCall(Model):
    id: str = ""
    name: str
    arguments: dict[str, Any]


class Scenario(Model):
    id: str
    pair_id: str
    seed: int
    family: Family
    benign: bool
    system: str
    prompt: str
    document: str
    canaries: list[str]
    tools: list[ToolSpec]
    allowed_tools: list[str]
    expected_text: str
    expected_tools: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class Request(Model):
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    seed: int = 0
    max_tokens: int = Field(default=512, ge=1, le=32768)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Usage(Model):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated: bool = False


class Response(Model):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(default=0, ge=0)
    request_envelope: dict[str, Any] = Field(default_factory=dict)
    response_envelope: dict[str, Any] = Field(default_factory=dict)
    guard_events: list[str] = Field(default_factory=list)
    raw_text: str | None = None
    raw_tool_calls: list[ToolCall] | None = None
    attempts: int = 1
    finish_reason: str | None = None
    truncated: bool = False
    model_digest: str | None = None

    @model_validator(mode="after")
    def native_finish_metadata(self) -> Response:
        # Backfill metadata from older direct-engine archives without changing their text.
        reason = self.finish_reason
        if reason is None:
            native = self.response_envelope
            if isinstance(native.get("done_reason"), str):
                reason = native["done_reason"]
            elif isinstance(native.get("choices"), list) and native["choices"]:
                choice = native["choices"][0]
                if isinstance(choice, dict) and isinstance(choice.get("finish_reason"), str):
                    reason = choice["finish_reason"]
        object.__setattr__(self, "finish_reason", reason)
        if reason in ("length", "max_tokens", "token_budget"):
            object.__setattr__(self, "truncated", True)
        return self


class Backend(Protocol):
    async def generate(self, request: Request) -> Response:
        """Generate a completion, without performing proposed tools."""
        ...


class Target(Protocol):
    async def respond(self, request: Request, scenario: Scenario) -> Response: ...


class JudgeVerdict(Model):
    outcome: Outcome
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence: str = Field(default="", max_length=2000)


class Verdict(Model):
    outcome: Outcome
    attack_success: bool = False
    false_refusal: bool = False
    findings: list[str] = Field(default_factory=list)
    semantic: JudgeVerdict | None = None
    disagreement: bool = False
    judge_error: str | None = None
    confidence: float = Field(default=1, ge=0, le=1)
    judge_outputs: list[str] = Field(default_factory=list)
    judge_validation_errors: list[str] = Field(default_factory=list)


class Turn(Model):
    id: str
    parent_id: str | None
    depth: int
    mutation: str
    prompt: str
    response: Response
    verdict: Verdict
    state: Literal["searching", "success", "exhausted", "control_complete"]
    attacker_usage: Usage = Field(default_factory=Usage)
    judge_usage: Usage = Field(default_factory=Usage)
    attacker_latency_ms: float = 0
    judge_latency_ms: float = 0
    strategy_score: float = 0
    attacker_outputs: list[str] = Field(default_factory=list)
    user_messages: list[str] = Field(default_factory=list)


class Trajectory(Model):
    scenario_id: str
    family: Family
    benign: bool
    provider: str
    mode: Literal["baseline", "hardened"]
    turns: list[Turn]


class PairResult(Model):
    key: str
    scenario: Scenario
    baseline: Trajectory
    hardened: Trajectory


class BackendConfig(Model):
    kind: Literal["ollama", "openai", "transformers"] = "ollama"
    model: str = "qwen2.5:7b-instruct-q4_K_M"
    endpoint: str = "http://127.0.0.1:11434"
    timeout: float = Field(default=120, gt=0)
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def pin_supported_engine(self) -> BackendConfig:
        if self.expected_digest and self.kind != "ollama":
            raise ValueError("Model digest pinning currently requires the Ollama backend")
        return self


class ProviderProfile(Model):
    name: Literal["direct", "openai", "anthropic", "azure", "bedrock"]
    jitter_ms: float = Field(default=0, ge=0, le=10000)
    rate_limit_probability: float = Field(default=0, ge=0, lt=1)
    retry_after_ms: float = Field(default=10, ge=0, le=10000)

    @model_validator(mode="after")
    def direct_has_no_simulation(self) -> ProviderProfile:
        if self.name == "direct" and (self.jitter_ms or self.rate_limit_probability):
            raise ValueError("Direct inference cannot simulate jitter or rate limits")
        return self


class GuardConfig(Model):
    spotlight: bool = True
    input_gate: bool = True
    output_enforcer: bool = True
    gate_threshold: int = Field(default=4, ge=1)


class RunConfig(Model):
    seed: int = Field(default=42, ge=0, le=2**63 - 1)
    cases_per_family: int = Field(default=3, ge=1, le=100000)
    max_turns: int = Field(default=3, ge=1, le=50)
    concurrency: int = Field(default=2, ge=1, le=128)
    retries: int = Field(default=2, ge=0, le=10)
    backoff_seconds: float = Field(default=0.05, ge=0, le=30)
    max_tokens: int = Field(default=512, ge=1, le=32768)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    attacker: BackendConfig | None = None
    judge: BackendConfig | None = None
    providers: list[ProviderProfile] = Field(
        default_factory=lambda: [ProviderProfile(name="direct")]
    )
    guard: GuardConfig = Field(default_factory=GuardConfig)
    deterministic_precedence: bool = True
    require_judge: bool = False
    replay_plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    max_total_tokens: int | None = Field(default=None, gt=0)
    max_run_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_profiles(self) -> RunConfig:
        names = [p.name for p in self.providers]
        if not names or len(set(names)) != len(names):
            raise ValueError("Choose at least one provider; provider names must be unique")
        if self.require_judge and self.judge is None:
            raise ValueError("require_judge needs a configured judge backend")
        return self


class Thresholds(Model):
    max_asr: float = Field(default=1, ge=0, le=1)
    max_frr: float = Field(default=1, ge=0, le=1)
    min_defense_delta: float = Field(default=-1, ge=-1, le=1)
    use_upper_ci: bool = False
    max_truncation_rate: float = Field(default=1, ge=0, le=1)


def derive_seed(seed: int, *parts: object) -> int:
    """Schedule-independent seed derivation; never depend on Python's salted hash."""
    encoded = json.dumps([seed, *parts], sort_keys=True).encode()
    return int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big") % (2**31)
