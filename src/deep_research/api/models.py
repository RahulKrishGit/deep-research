"""Strict request, session, trace, and error models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from deep_research.utils.config import ConfigSettings, apply_config_overrides
from deep_research.utils.text import collapse_whitespace
from deep_research.utils.types import (
    FigureAttribution,
    FigureDropReason,
    FigureKind,
    FindingDropReason,
    FindingStatus,
    ResearchError,
    SourceEvaluationStatus,
)

# ``needs_input`` is the one waiting status (live-briefs spec §4.4): the one-time
# check is waiting for the reader's answers. It is not terminal, and it returns to
# ``running`` when the answers arrive, the reader skips, or the wait times out.
SessionStatus = Literal[
    "running",
    "needs_input",
    "completed",
    "max_iterations",
    "incomplete",
    "failed",
]


class ApiModel(BaseModel):
    """Base validation behavior shared by every API model.

    ``extra="forbid"`` keeps a misspelled field from being silently ignored,
    ``str_strip_whitespace`` normalizes request text before length checks,
    and ``validate_default`` runs defaults through the same validation as
    explicit input.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class ResearchRequest(ApiModel):
    """One validated request to start a research session.

    ``max_iterations`` keeps the name existing clients send (PD-15) and is the
    ceiling on *extra* research passes: zero is a legitimate request — a run
    that may buy no extra pass for a missing required target — so the bound is
    ``>= 0`` rather than ``>= 1``.

    ``ask_clarifying_questions`` turns the one-time check on (live-briefs spec
    §4.4, D16): before planning, a question that leaves something material open
    gets up to three questions for the reader. Off, no check call is made.
    """

    query: str = Field(min_length=1)
    max_iterations: int | None = Field(default=None, ge=0)
    output_format: Literal["markdown"] = "markdown"
    config_overrides: dict[str, JsonValue] = Field(default_factory=dict)
    ask_clarifying_questions: bool = True

    @field_validator("config_overrides")
    @classmethod
    def validate_config_overrides(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Reject unknown or invalid override paths before a session starts.

        Validating against the default settings rejects paths the
        configuration never had without touching a file or a secret.
        """
        apply_config_overrides(ConfigSettings(), value)
        return value


class ClarificationAnswer(ApiModel):
    """One answer to the one-time check: an offered option, or the reader's own text.

    ``question_id`` and ``choice`` are capped at what a check can ask (``q1``..``q3``,
    options of at most 80 characters). ``text`` is collapsed to one single-spaced
    line before its length is checked, so a typed answer can never start a new
    line in the planner's ``# Reader answers`` section.
    """

    question_id: str = Field(min_length=1, max_length=8)
    choice: str | None = Field(default=None, min_length=1, max_length=80)
    text: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("text", mode="before")
    @classmethod
    def one_line(cls, value: object) -> object:
        return collapse_whitespace(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def exactly_one_answer(self) -> ClarificationAnswer:
        if (self.choice is None) == (self.text is None):
            raise ValueError("an answer carries exactly one of choice or text")
        return self


class ClarificationAnswersRequest(ApiModel):
    """``POST /research/{id}/answers``: the reader's answers, once (spec §4.4).

    A question left out takes its best guess. ``skip`` records that the reader
    chose to start now ("Just start") with whatever they had answered.
    """

    answers: list[ClarificationAnswer] = Field(default_factory=list, max_length=3)
    skip: bool = False


class CoverageProgressResponse(ApiModel):
    """Required-target progress, and what the report could not answer.

    Two readings of one denominator, kept apart: ``missing_required_target_ids``
    is the gate's own reading — every required target no verified finding
    answers — while ``not_found_target_ids`` is the report's own account of
    what it searched for and did not find. A missing target the report lists
    under Not found is accounted for; a missing target no list names is the
    gate failure.
    """

    required_targets: int = Field(ge=0)
    answered_targets: int = Field(ge=0)
    missing_required_target_ids: list[str] = Field(default_factory=list)
    not_found_target_ids: list[str] = Field(default_factory=list)


class EvidenceCountsResponse(ApiModel):
    """Distinct quantities, each of a different thing (Section 2.5).

    A read call is not a work, a work is not a publisher, a source URL is not
    a finding, and "checked" is not "cited". Each field here answers a
    question the others cannot, which is why none of them is an alias of
    another and why a read-call count never stands in for unique works.

    The five findings-side readings are the Evidence Verifier's own: confirmed
    as written, kept with corrected context, dropped, kept with an unchecked
    context, and cited by the reader report.
    """

    read_records: int = Field(ge=0)
    network_reads: int = Field(ge=0)
    cache_reads: int = Field(ge=0)
    unique_works: int = Field(ge=0)
    publishers: int = Field(ge=0)
    source_urls: int = Field(ge=0)
    findings: int = Field(ge=0)
    assessed_sources: int = Field(ge=0)
    cited_assessed_sources: int = Field(ge=0)
    verified_findings: int = Field(ge=0)
    corrected_findings: int = Field(ge=0)
    quoted_findings: int = Field(ge=0)
    dropped_findings: int = Field(ge=0)
    context_unchecked_findings: int = Field(ge=0)
    cited_findings: int = Field(ge=0)


class ResearchSessionResponse(ApiModel):
    """The immutable snapshot of one session the API returns.

    The first three fields exist from the moment a session starts; everything
    from ``evidence_path`` down is the finished run's own reading, so a
    running session answers ``None`` for each of them rather than a zero or a
    default it never measured.
    """

    session_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    """The research question the session runs — the request's ``query``, stripped."""
    status: SessionStatus
    current_agent: str | None = None
    iteration: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None = None
    report_path: str | None = None
    trace_url: str | None = None
    errors: list[ResearchError] = Field(default_factory=list)
    evidence_path: str | None = None
    """The file the evidence ledger was published under, or ``None``."""

    quality_path: str | None = None
    """The file the quality record was published under, or ``None``.

    The three paths are one published set: all of them are advertised together
    or none is, so a missing quality path is an incomplete publication rather
    than a file to look for elsewhere.
    """

    quality_contract_version: str | None = None
    """Which evidence/quality contract wrote this session's snapshot."""

    semantic_review_status: str | None = None
    """The terminal review's own status, or ``None`` when none was recorded."""

    semantic_review_score: float | None = None
    """The review's mean over its dimensions, or ``None`` without one.

    ``None`` is "no score was recorded", never a zero: an incomplete or
    provider-failed review has no score, and reporting one would be inventing
    a judgement nobody made.
    """

    duration_seconds: float | None = Field(default=None, ge=0)
    """The span the session's recorded events cover, or ``None``."""

    coverage: CoverageProgressResponse | None = None
    """Target and topic progress, or ``None`` when nothing judged it."""

    evidence_counts: EvidenceCountsResponse | None = None
    """The distinct counts, or ``None`` without a composition to count."""


class SessionListResponse(ApiModel):
    """The process's sessions, newest first; each item is that session's
    status response.
    """

    sessions: list[ResearchSessionResponse] = Field(default_factory=list)


class EvidenceModel(BaseModel):
    """Base for the evidence view's models: strict shape, verbatim text.

    Unlike ``ApiModel`` it does not strip strings: ``content``, ``snippet``,
    ``passage`` and ``evidence_words`` are verbatim page text and must reach
    the client exactly as the run recorded them.
    """

    model_config = ConfigDict(extra="forbid", validate_default=True)


class EvidenceSourceResponse(EvidenceModel):
    url: str
    title: str
    organisation: str
    evaluation_status: SourceEvaluationStatus | None = None
    """``None`` when the composition holds no ``ScoredSource`` for this URL."""
    low_confidence: bool = False
    authority_score: float | None = None
    recency_score: float | None = None
    relevance_score: float | None = None
    overall_score: float | None = None


class EvidenceFigureResponse(EvidenceModel):
    value: str
    kept: bool
    period: str | None
    scope: str | None
    organisation: str | None
    attribution: FigureAttribution | None
    kind: FigureKind | None
    release: str | None
    evidence_words: str | None
    corrected: bool
    dropped_reason: FigureDropReason | None
    reason: str | None


class EvidenceFindingResponse(EvidenceModel):
    label: str
    status: FindingStatus | None
    dropped_reason: FindingDropReason | None
    context_unchecked: bool
    cited: bool
    target_ids: list[str]
    content: str
    snippet: str | None
    passage: str | None
    source: EvidenceSourceResponse
    figures: list[EvidenceFigureResponse]


class EvidenceNotFoundResponse(EvidenceModel):
    target_id: str
    question: str
    queries: list[str]
    pages_read: list[str]
    searched: bool


class EvidenceRefusedResponse(EvidenceModel):
    where: str
    text: str
    reason: str
    finding_labels: list[str]


class EvidenceResponse(EvidenceModel):
    """E1: every finding with its verification and source, the not-found
    targets, the refused sentences.
    """

    session_id: str
    iteration: int
    findings: list[EvidenceFindingResponse]
    not_found: list[EvidenceNotFoundResponse]
    refused: list[EvidenceRefusedResponse]


class TraceMetadata(ApiModel):
    """Session and route facts one trace response carries."""

    session_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    status: SessionStatus


class TraceResponse(ApiModel):
    """What one session's trace endpoint returns."""

    session_id: str = Field(min_length=1)
    trace_url: str | None = None
    metadata: TraceMetadata


class ValidationIssue(ApiModel):
    """One request-validation finding: location and enumerated type only.

    Rejected input values are deliberately absent: a client that sent a
    secret in a rejected field must not get it echoed back.
    """

    location: str = Field(min_length=1)
    type: str = Field(min_length=1)


class ApiErrorBody(ApiModel):
    """The structured error payload every API error response carries."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    reason: str | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)


class ApiErrorResponse(ApiModel):
    """The envelope wrapping one ``ApiErrorBody``."""

    error: ApiErrorBody
