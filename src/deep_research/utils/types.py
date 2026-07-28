"""Shared typed contracts for research state and domain data."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from typing import Annotated, Literal, TypeAlias, TypedDict

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)


def _validate_aware_iso8601(value: str) -> str:
    """Require an ISO 8601 timestamp with timezone information."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be a valid ISO 8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_finite_json(value: JsonValue) -> JsonValue:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, list):
        for item in value:
            _validate_finite_json(item)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_finite_json(item)
    return value


AwareISOString: TypeAlias = Annotated[str, AfterValidator(_validate_aware_iso8601)]
_FiniteJsonValue: TypeAlias = Annotated[
    JsonValue,
    AfterValidator(_validate_finite_json),
]
UnitScore: TypeAlias = Annotated[float, Field(ge=0.0, le=1.0)]
CriticScore: TypeAlias = Annotated[int, Field(ge=1, le=10)]
Priority: TypeAlias = Annotated[int, Field(ge=1)]
ClaimVerdict: TypeAlias = Literal[
    "verified",
    "unverified",
    "contradicted",
    "insufficient_evidence",
]


class ContractModel(BaseModel):
    """Base validation behavior shared by public research contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class SubTopic(ContractModel):
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    search_queries: list[str] = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    priority: Priority


class Finding(ContractModel):
    content: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    extracted_at: AwareISOString
    confidence: UnitScore
    related_sub_topic: str = Field(min_length=1)


class ScoredSource(ContractModel):
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authority_score: UnitScore
    recency_score: UnitScore
    relevance_score: UnitScore
    corroboration_score: UnitScore
    overall_score: UnitScore
    rationale: str = Field(min_length=1)


class Claim(ContractModel):
    text: str = Field(min_length=1)
    source_urls: list[str] = Field(min_length=1)
    verdict: ClaimVerdict
    confidence: UnitScore
    evidence: list[str]
    contradictions: list[str]


class Critique(ContractModel):
    score: CriticScore
    gaps: list[str]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    should_continue: bool
    rationale: str = Field(min_length=1)


class MemorySnapshot(ContractModel):
    similar_findings: list[Finding] = Field(default_factory=list)
    known_source_reputations: dict[str, UnitScore] = Field(default_factory=dict)
    suggested_strategies: list[str] = Field(default_factory=list)


class ResearchEvent(ContractModel):
    event_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    metadata: dict[str, _FiniteJsonValue] = Field(default_factory=dict)


class ResearchError(ContractModel):
    error_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool = True
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    details: dict[str, _FiniteJsonValue] = Field(default_factory=dict)


class ResearchState(ContractModel):
    session_id: str = Field(min_length=1)
    original_question: str = Field(min_length=1)
    sub_topics: list[SubTopic] = Field(default_factory=list)
    raw_findings: list[Finding] = Field(default_factory=list)
    evaluated_sources: list[ScoredSource] = Field(default_factory=list)
    verified_claims: list[Claim] = Field(default_factory=list)
    report: str | None = None
    critique: Critique | None = None
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=3, ge=1)
    memory_context: MemorySnapshot = Field(default_factory=MemorySnapshot)
    events: list[ResearchEvent] = Field(default_factory=list)
    errors: list[ResearchError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_iteration_bounds(self) -> ResearchState:
        if self.iteration > self.max_iterations:
            raise ValueError("iteration cannot exceed max_iterations")
        return self


class ResearchStateUpdate(TypedDict, total=False):
    session_id: str
    original_question: str
    sub_topics: list[SubTopic]
    raw_findings: list[Finding]
    evaluated_sources: list[ScoredSource]
    verified_claims: list[Claim]
    report: str | None
    critique: Critique | None
    max_iterations: int
    memory_context: MemorySnapshot
    events: list[ResearchEvent]
    errors: list[ResearchError]


_APPEND_STATE_FIELDS = frozenset(
    {
        "sub_topics",
        "raw_findings",
        "evaluated_sources",
        "verified_claims",
        "events",
        "errors",
    }
)


def merge_research_state(
    state: ResearchState,
    update: ResearchStateUpdate,
) -> ResearchState:
    unknown_fields = set(update).difference(ResearchState.model_fields)
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown ResearchState fields: {names}")
    if "iteration" in update:
        raise ValueError("use advance_research_iteration to change iteration")

    payload = state.model_dump(mode="python")
    for field_name, value in update.items():
        if field_name in _APPEND_STATE_FIELDS:
            if not isinstance(value, list):
                raise TypeError(f"{field_name} update must be a list")
            payload[field_name] = [*payload[field_name], *deepcopy(value)]
        else:
            payload[field_name] = deepcopy(value)

    return ResearchState.model_validate(payload)


def advance_research_iteration(state: ResearchState) -> ResearchState:
    if state.iteration >= state.max_iterations:
        raise ValueError("cannot advance iteration beyond max_iterations")

    payload = state.model_dump(mode="python")
    payload["iteration"] = state.iteration + 1
    return ResearchState.model_validate(payload)
