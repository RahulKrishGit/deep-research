"""Shared typed contracts for research state and domain data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, TypeAlias

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue


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


AwareISOString: TypeAlias = Annotated[str, AfterValidator(_validate_aware_iso8601)]
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
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ResearchError(ContractModel):
    error_type: str = Field(min_length=1)
    source: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool = True
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    details: dict[str, JsonValue] = Field(default_factory=dict)
