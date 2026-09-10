"""Validated, framework-light presentation contracts for the Streamlit UI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from deep_research.utils.types import ClaimVerdict, ContractModel, ResearchError

UiSessionStatus = Literal[
    "running",
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
]

UiCredibilityTier = Literal["high", "moderate", "low", "unrated"]


class UiTokenUsage(ContractModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UiToolCallSummary(ContractModel):
    tool_name: str = Field(min_length=1)
    display_label: str = Field(min_length=1)
    calls: int = Field(ge=0)
    failures: int = Field(ge=0)


class UiExecutionErrorPresentation(ContractModel):
    """Safe, project-owned copy for one execution error in the UI."""

    category: str = Field(min_length=1)
    message: str = Field(min_length=1)
    effect: str = Field(min_length=1)
    recovery_hint: str | None = None
    diagnostic_context: dict[str, str] = Field(default_factory=dict)


class UiSubTopicProgress(ContractModel):
    index: int = Field(ge=1)
    title: str = Field(min_length=1)
    status: Literal["queued", "running", "completed"]
    priority: int | None = Field(default=None, ge=1)
    findings: int | None = Field(default=None, ge=0)


class UiRecentActivity(ContractModel):
    event_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class UiSourceDetail(ContractModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    tier: UiCredibilityTier
    overall_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    corroboration_score: float = Field(ge=0.0, le=1.0)
    related_sub_topics: list[str] = Field(default_factory=list)


class UiSourceSummary(ContractModel):
    total: int = Field(ge=0)
    high: int = Field(ge=0)
    moderate: int = Field(ge=0)
    low: int = Field(ge=0)
    unrated: int = Field(ge=0)
    details: list[UiSourceDetail] = Field(default_factory=list)


class UiClaimDetail(ContractModel):
    text: str = Field(min_length=1)
    verdict: ClaimVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class UiFactCheckSummary(ContractModel):
    verified: int = Field(ge=0)
    unverified: int = Field(ge=0)
    contradicted: int = Field(ge=0)
    insufficient_evidence: int = Field(ge=0)
    details: list[UiClaimDetail] = Field(default_factory=list)


class UiSessionSnapshot(ContractModel):
    session_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    status: UiSessionStatus
    started_at: datetime
    finished_at: datetime | None = None
    current_agent: str | None = None
    last_agent: str | None = None
    iteration: int = Field(ge=0)
    max_iterations: int = Field(ge=1)
    planned_sub_topic_count: int = Field(default=0, ge=0)
    research_phase_complete: bool = False
    sub_topics: list[UiSubTopicProgress] = Field(default_factory=list)
    last_sub_topic: UiSubTopicProgress | None = None
    recent_activity: list[UiRecentActivity] = Field(default_factory=list)
    tool_calls: list[UiToolCallSummary] = Field(default_factory=list)
    token_usage: UiTokenUsage | None = None
    trace_url: str | None = None
    report_path: str | None = None
    report: str | None = None
    source_summary: UiSourceSummary
    fact_check_summary: UiFactCheckSummary
    errors: list[ResearchError] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    events_seen: int = Field(default=0, ge=0)


def _sanitize_history_error(error: object) -> object:
    """Retain structured error metadata while dropping free-form details."""
    if isinstance(error, ResearchError):
        return ResearchError.model_validate(error.model_dump(exclude={"details"}))
    if isinstance(error, Mapping):
        return {key: value for key, value in error.items() if key != "details"}
    return error


class SessionHistoryEntry(ContractModel):
    session_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    status: UiSessionStatus
    started_at: datetime
    finished_at: datetime | None = None
    current_agent: str | None = None
    last_agent: str | None = None
    iteration: int = Field(ge=0)
    max_iterations: int = Field(ge=1)
    planned_sub_topic_count: int = Field(default=0, ge=0)
    research_phase_complete: bool = False
    sub_topics: list[UiSubTopicProgress] = Field(default_factory=list)
    last_sub_topic: UiSubTopicProgress | None = None
    recent_activity: list[UiRecentActivity] = Field(default_factory=list)
    report_path: str | None = None
    trace_url: str | None = None
    token_usage: UiTokenUsage | None = None
    source_summary: UiSourceSummary
    fact_check_summary: UiFactCheckSummary
    errors: list[ResearchError] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @field_validator("errors", mode="before")
    @classmethod
    def sanitize_errors(cls, value: object) -> object:
        if isinstance(value, Iterable) and not isinstance(
            value, (str, bytes, bytearray, Mapping)
        ):
            return [_sanitize_history_error(error) for error in value]
        return value

    @field_validator("recent_activity", mode="before")
    @classmethod
    def cap_recent_activity(cls, value: object) -> object:
        if isinstance(value, Iterable) and not isinstance(
            value, (str, bytes, bytearray, Mapping)
        ):
            return list(value)[-3:]
        return value


def history_entry_from_snapshot(snapshot: UiSessionSnapshot) -> SessionHistoryEntry:
    """Build a compact history record without persisting report detail payloads."""
    source_summary = UiSourceSummary.model_validate(
        snapshot.source_summary.model_dump(exclude={"details"})
    )
    fact_check_summary = UiFactCheckSummary.model_validate(
        snapshot.fact_check_summary.model_dump(exclude={"details"})
    )
    return SessionHistoryEntry(
        session_id=snapshot.session_id,
        question=snapshot.question,
        status=snapshot.status,
        started_at=snapshot.started_at,
        finished_at=snapshot.finished_at,
        current_agent=snapshot.current_agent,
        last_agent=snapshot.last_agent,
        iteration=snapshot.iteration,
        max_iterations=snapshot.max_iterations,
        planned_sub_topic_count=snapshot.planned_sub_topic_count,
        research_phase_complete=snapshot.research_phase_complete,
        sub_topics=[topic.model_copy(deep=True) for topic in snapshot.sub_topics],
        last_sub_topic=(
            snapshot.last_sub_topic.model_copy(deep=True)
            if snapshot.last_sub_topic is not None
            else None
        ),
        recent_activity=[
            activity.model_copy(deep=True)
            for activity in snapshot.recent_activity[-3:]
        ],
        report_path=snapshot.report_path,
        trace_url=snapshot.trace_url,
        token_usage=snapshot.token_usage,
        source_summary=source_summary,
        fact_check_summary=fact_check_summary,
        errors=[_sanitize_history_error(error) for error in snapshot.errors],
        limitations=list(snapshot.limitations),
    )


__all__ = [
    "SessionHistoryEntry",
    "UiClaimDetail",
    "UiCredibilityTier",
    "UiExecutionErrorPresentation",
    "UiFactCheckSummary",
    "UiRecentActivity",
    "UiSessionSnapshot",
    "UiSessionStatus",
    "UiSourceDetail",
    "UiSourceSummary",
    "UiSubTopicProgress",
    "UiTokenUsage",
    "UiToolCallSummary",
    "history_entry_from_snapshot",
]
