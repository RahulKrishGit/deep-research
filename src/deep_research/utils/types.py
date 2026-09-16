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
SourceEvaluationStatus: TypeAlias = Literal[
    "scored",
    "unscored_cap",
    "unscored_provider",
    "unscored_missing",
]


class ContractModel(BaseModel):
    """Base validation behavior shared by public research contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class SubTopic(ContractModel):
    coverage_id: str = Field(min_length=1)
    """The identity of one planned sub-topic, such as ``topic-01``.

    Stamped locally by ``PlannerAgent`` once a plan validates, in priority
    order — the provider-facing ``planner.ResearchPlanDraft`` carries no such
    field, so no model ever proposes one. Later stages name the planned
    sub-topic they answered, skipped, or never reached by this id.
    """

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
    authority_score: UnitScore | None = None
    recency_score: UnitScore | None = None
    relevance_score: UnitScore | None = None
    overall_score: UnitScore | None = None
    rationale: str = Field(min_length=1)
    evaluation_status: SourceEvaluationStatus = "scored"
    low_confidence: bool = False
    """True when this source must not be leaned on without more evidence.

    Set explicitly by ``SourceEvaluatorAgent`` when a scored source falls
    under its threshold. An unscored source uses ``evaluation_status`` to
    explain why it has no quality judgement and never carries this flag.
    """

    @model_validator(mode="after")
    def validate_evaluation_status(self) -> ScoredSource:
        scores = (
            self.authority_score,
            self.recency_score,
            self.relevance_score,
            self.overall_score,
        )
        if self.evaluation_status == "scored":
            if any(score is None for score in scores):
                raise ValueError(
                    "scored sources require all quality scores"
                )
        elif any(score is not None for score in scores):
            raise ValueError(
                "unscored sources must not carry quality scores"
            )
        elif self.low_confidence:
            raise ValueError(
                "unscored sources must not carry low_confidence"
            )
        return self


class EvidencePassage(ContractModel):
    """One provenance-bearing passage used to judge a claim."""

    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    stance: Literal["supports", "contradicts"]


# Task 5 provenance bounds. ``consumed_finding_fingerprints`` and
# ``consumed_coverage_ids`` record what one claim already consumed so a later
# pass can tell unchanged evidence from new evidence. They are bounded so a
# claim record cannot grow without limit; the truncation polarity is
# deliberate: an identity that did not fit is treated as not consumed, which
# costs extra work and can never silently skip evidence.
MAX_CONSUMED_FINDING_FINGERPRINTS = 64
MAX_CONSUMED_COVERAGE_IDS = 32


class Claim(ContractModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_urls: list[str] = Field(min_length=1)
    verdict: ClaimVerdict
    confidence: UnitScore
    evidence: list[str]
    contradictions: list[str]
    verification_evidence: list[EvidencePassage]
    # Why this claim could not be judged, and ``None`` for every claim that
    # was. Set only when ``verdict == "insufficient_evidence"``, from the Fact
    # Checker's enumerated ``INSUFFICIENT_REASONS`` set, so that "nothing
    # independent was ever read" stays distinguishable from "the verdict came
    # back thin" — in the published artifacts, not only in the event log a
    # reviewer never sees.
    #
    # The type is a plain ``str`` and deliberately not that enumeration:
    # ``INSUFFICIENT_REASONS`` belongs to the agent, and this module is the
    # shared contract layer every agent reads, so importing it here would
    # invert the dependency. It carries no ``Field`` constraint for the same
    # reason — a snapshot written by a later release, naming a reason this
    # one does not know, must stay readable rather than fail validation.
    insufficient_reason: str | None = None
    # The origin findings and planned coverage topics this claim already
    # consumed, as stable identities. Both default to empty, which suppresses
    # nothing: a claim carrying no recorded provenance (a fixture, or a
    # snapshot written before provenance existed) makes every finding look new
    # so the next pass re-checks rather than skips.
    consumed_finding_fingerprints: list[str] = Field(
        default_factory=list,
        max_length=MAX_CONSUMED_FINDING_FINGERPRINTS,
    )
    consumed_coverage_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_CONSUMED_COVERAGE_IDS,
    )


class ReportQualitySnapshot(ContractModel):
    """Deterministic integrity and coverage metrics for one report pass."""

    coverage_ratio: UnitScore
    planned_topics: int = Field(ge=0)
    covered_topics: int = Field(ge=0)
    unresolved_topic_ids: list[str] = Field(default_factory=list)
    unique_findings: int = Field(ge=0)
    unique_sources: int = Field(ge=0)
    cited_sources: int = Field(ge=0)
    scored_cited_source_ratio: UnitScore
    verified_claims: int = Field(ge=0)
    contradicted_claims: int = Field(ge=0)
    duplicate_claims: int = Field(ge=0)
    duplicate_source_rows: int = Field(ge=0)
    uncited_settled_points: int = Field(ge=0)
    hard_failures: list[str] = Field(default_factory=list)


class CritiqueGap(ContractModel):
    """One targetable report problem returned by the Critic."""

    coverage_id: str | None = None
    problem: str = Field(min_length=1)
    recommended_queries: list[str] = Field(default_factory=list)


class Critique(ContractModel):
    score: CriticScore
    gaps: list[CritiqueGap]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    should_continue: bool
    rationale: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_gap_strings(cls, values: object) -> object:
        """Accept older state fixtures while normalizing to targetable gaps.

        The rule itself lives in ``agents.critic.normalize_gap_drafts`` and is
        imported at call time: ``agents.critic`` imports this module, so a
        module-level import would be a cycle, and a validator only ever runs
        once every module is loaded. Sharing the one implementation is what
        keeps this boundary and the provider-facing ``CritiqueDraft`` from
        reading the same legacy input differently.
        """
        from deep_research.agents.critic import (  # noqa: PLC0415
            normalize_gap_drafts,
        )

        return normalize_gap_drafts(values)


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


# The quality status one composed report carries, and when it carries it.
# ``NOT_GATED`` is the honest value for a report the terminal gates have not
# judged yet: the reader report must always declare a status, and a blank
# would say nothing at all. The status is derived from the same routing
# decision the reader report's own gates produced — see
# ``graph.state.graph_quality_status``.
QUALITY_STATUS_NOT_GATED = "not yet quality-gated"
QUALITY_STATUS_ACCEPTED = "accepted"
QUALITY_STATUS_PARTIAL = "partial"


class Citation(ContractModel):
    """One numbered source reference."""

    number: int = Field(ge=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)


class ReportPoint(ContractModel):
    """One settled statement, with the claims and sources it rests on.

    ``claim_ids`` and ``source_urls`` are already validated against the
    checked-claim registry by the time a point reaches a renderer; rendering
    never validates.
    """

    text: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class ReportConstraint(ReportPoint):
    """One ranked constraint, plus the two decision columns it prints.

    A constraint row is a claim-linked point like any other; the deployment
    mechanism and the geography are part of the same claim-backed row, and a
    row whose evidence does not state them says ``not stated``.
    """

    deployment_mechanism: str = ""
    geography: str = ""


class ReportSection(ContractModel):
    """One validated theme of the findings, as claim-linked points."""

    title: str = Field(min_length=1)
    points: list[ReportPoint] = Field(default_factory=list)


class ReportComposition(ContractModel):
    """Everything one synthesis pass composed, and the evidence it renders.

    Built once per pass by the Synthesizer and handed to both renderers, so
    the two artifacts can never disagree about the same pass. ``claims`` and
    ``sources`` are canonicalized on construction, which is what makes "one
    row per canonical record" a property of the type rather than of the
    caller.

    It lives here, beside the rest of the research state, because
    ``ResearchState`` carries the exact composition the terminal quality pass
    judged. The canonicalization helpers are imported inside the validator
    rather than at module scope: ``agents.identity`` imports this module, so
    a module-level import would be a cycle, and a validator only ever runs
    once every module is loaded.
    """

    question: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=0, ge=0)
    as_of: str = ""
    """The newest timestamp the recorded evidence carries; see report_as_of."""
    scope: str = ""
    """The scope this report assumes; see report_scope."""
    quality_status: str = QUALITY_STATUS_NOT_GATED
    sub_topics: list[SubTopic] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    sources: list[ScoredSource] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    errors: list[ResearchError] = Field(default_factory=list)
    summary: list[ReportPoint] = Field(default_factory=list)
    constraints: list[ReportConstraint] = Field(default_factory=list)
    sections: list[ReportSection] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    """Drafted content this pass refused, as project-generated reasons."""

    @model_validator(mode="after")
    def canonicalize_evidence(self) -> ReportComposition:
        from deep_research.agents.identity import (  # noqa: PLC0415
            merge_claim_snapshot,
            merge_source_snapshot,
        )

        self.sources = merge_source_snapshot([], self.sources)
        self.claims = merge_claim_snapshot([], self.claims)
        return self


class ResearchState(ContractModel):
    session_id: str = Field(min_length=1)
    original_question: str = Field(min_length=1)
    sub_topics: list[SubTopic] = Field(default_factory=list)
    raw_findings: list[Finding] = Field(default_factory=list)
    evaluated_sources: list[ScoredSource] = Field(default_factory=list)
    verified_claims: list[Claim] = Field(default_factory=list)
    report: str | None = None
    """The reader report Markdown composed for the latest pass."""
    report_path: str | None = None
    """The file the reader report was published under, or ``None``.

    Written only by the terminal finalizer, from the write that actually
    succeeded. ``None`` means the Markdown in ``report`` was never published —
    a caller must never fall back to an earlier pass's file.
    """
    report_evidence: str | None = None
    """The evidence ledger Markdown composed for the same pass as ``report``.

    Composed, never published: the terminal publication step is the only
    writer. Both strings are authoritative in state whether or not any file
    exists.
    """
    evidence_path: str | None = None
    """The file the evidence ledger was published under, or ``None``.

    Written only by the terminal finalizer, from the write that actually
    succeeded; ``None`` until a ledger has been published.
    """
    composition: ReportComposition | None = None
    """The typed composition ``report`` and ``report_evidence`` render.

    Carried in state so the quality pass judges the exact points and the
    exact canonical evidence one pass composed, rather than re-deriving them
    from Markdown.
    """
    quality: ReportQualitySnapshot | None = None
    """The deterministic quality snapshot for ``composition``.

    ``None`` until a synthesis pass has composed artifacts to judge. The
    terminal route and the terminal quality status are both read from it.
    """
    unique_source_count: int = Field(default=0, ge=0)
    """Canonical reviewed sources behind ``report`` — one per source URL."""
    unique_claim_count: int = Field(default=0, ge=0)
    """Canonical checked claims behind ``report`` — one per claim identity."""
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
    report_path: str | None
    report_evidence: str | None
    evidence_path: str | None
    composition: ReportComposition | None
    quality: ReportQualitySnapshot | None
    unique_source_count: int
    unique_claim_count: int
    critique: Critique | None
    max_iterations: int
    memory_context: MemorySnapshot
    events: list[ResearchEvent]
    errors: list[ResearchError]


# Fields whose update is a delta appended to what the state already holds.
# ``evaluated_sources`` and ``verified_claims`` are deliberately absent: each
# carries the complete canonical snapshot for the run so far, which its
# producer — Source Evaluator or Fact Checker — merges from
# ``deep_research.agents.identity`` before writing. Appending them instead
# stored one more copy of every source and claim per research pass.
_APPEND_STATE_FIELDS = frozenset(
    {
        "sub_topics",
        "raw_findings",
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
