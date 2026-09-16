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


# The versioned evidence contract this build writes, and the value a snapshot
# written before it carries. Legacy snapshots keep reading: they load with the
# legacy version and empty registries, so no read provenance is ever
# synthesized for a run that recorded none, and a consumer can tell a
# pre-contract snapshot from a current-contract one instead of assuming.
QUALITY_CONTRACT_VERSION = "1"
LEGACY_QUALITY_CONTRACT_VERSION = "0"


class _VerbatimContractModel(ContractModel):
    """Contract model that keeps extracted source text exactly as read.

    ``ContractModel`` strips whitespace from every string recursively, and
    pydantic applies that to nested values too. A read registry stores
    extracted document text and the excerpt locators taken from it, so
    stripping would silently rewrite the evidence — the same reason
    ``tools.base.ToolResult`` sets no strip policy. Only these models opt out.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        validate_default=True,
    )


class WorkIdentity(ContractModel):
    """The identity of one intellectual work, or an explicit ambiguity.

    ``key`` is ``None`` whenever the work is not established — either because
    the evidence is missing (``unknown``) or because it contradicts itself
    (``conflicting``). Consumers must treat a ``None`` key as "cannot
    establish this is the same work", never as a new work: unknown identity
    cannot establish independence either. ``aliases`` keeps every alias that
    was considered, so an unresolved ambiguity stays auditable instead of
    being flattened into one key.
    """

    key: str | None = None
    aliases: list[str] = Field(default_factory=list)
    basis: str = Field(min_length=1)
    """The project-generated sentence naming what established the identity."""
    issuer_id: str | None = None
    derives_from_work_ids: list[str] = Field(default_factory=list)
    """Evidenced version/derivation relationships, which never merge works."""
    identity_status: Literal["known", "unknown", "conflicting"]


class ReadRecord(_VerbatimContractModel):
    """One successful read of one source, complete enough to be replayed.

    The record is the unit of read provenance: a read ID, both URLs the
    transport actually used, the hash of the complete extracted text, the
    locator-keyed text itself, and the session that read it. Nothing mutable —
    no score, no verdict, no assessment — is stored here, so a later pass
    cannot silently re-identify evidence by rewriting its assessment.
    """

    read_id: str = Field(min_length=1)
    requested_url: str = Field(min_length=1)
    resolved_url: str = Field(min_length=1)
    """The URL the content was served from, after any redirect."""
    title: str = Field(min_length=1)
    reader: Literal["web_scraper", "document_reader"]
    retrieved_at: AwareISOString
    """When this body was observed — preserved across cache admission."""
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    """SHA-256 of the normalized complete text; never an empty or partial hash."""
    extraction_complete: bool
    passages: dict[str, str] = Field(min_length=1)
    """Locator -> the extracted text at that locator, verbatim."""
    target_ids: list[str] = Field(default_factory=list)
    acquisition_kind: Literal["network", "cache"] = "network"
    """``cache`` only for a record this session validated locally."""
    origin_session_id: str = Field(min_length=1)
    """The session that read the bytes; never the session that imported them."""
    version_validated_at: AwareISOString | None = None
    """When a time-sensitive cached version was last validated locally."""


class EvidenceUnit(_VerbatimContractModel):
    """One exact passage of one read, tied to the targets it may support."""

    evidence_id: str = Field(min_length=1)
    read_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    origin: Literal["researcher", "fact_checker"]


class EvidenceTarget(ContractModel):
    """One answerable obligation a planned topic must satisfy.

    Which support policy applies is decided before verdicts are assigned, and
    the planner stamps it here so no later stage can downgrade an obligation
    to pass a coverage gate.
    """

    target_id: str = Field(min_length=1)
    coverage_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    required_dimensions: list[str] = Field(min_length=1)
    required: bool
    critical: bool
    support_policy: Literal[
        "independent_pair",
        "primary_attribution",
        "derivation",
    ]


class EvidenceDisposition(ContractModel):
    """Why one item was not admitted, retained, or handed on.

    ``stage`` and ``reason`` are plain strings, deliberately: the enumerated
    vocabularies live beside the producers (``agents.evidence``), so a
    snapshot written by a later release that names a stage this one does not
    know stays readable rather than failing validation — the same reasoning
    as ``Claim.insufficient_reason``.
    """

    item_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    retained_equivalent_id: str | None = None
    """The retained evidence an omitted duplicate is equivalent to."""


class BoundaryAudit(ContractModel):
    """One Section 2.6 boundary manifest: what crossed one handoff.

    Persisted per boundary — read admission, passage selection, and the later
    handoffs tasks 5-10 add — so a replay can name the exact first missing
    boundary instead of reporting "not recorded". A missing manifest must
    fail a replay assertion (``evidence.require_boundary_manifest``); it must
    never be read as zero loss.
    """

    audit_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    claim_cluster_ids: list[str] = Field(default_factory=list)
    input_ids: list[str] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)
    returned_ids: list[str] = Field(default_factory=list)
    accepted_ids: list[str] = Field(default_factory=list)
    deferred_ids: list[str] = Field(default_factory=list)
    disposition_ids: list[str] = Field(default_factory=list)
    packet_fingerprint: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    configuration_fingerprint: str = Field(min_length=1)
    status: Literal["completed", "deferred", "provider_failed", "schema_failed"]


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
    read_records: dict[str, ReadRecord] = Field(default_factory=dict)
    """Every successful read of this run so far, keyed by ``read_id``.

    Carried in state so a later pass, agent, or replay can resolve the exact
    body a passage came from instead of re-fetching it. A snapshot written
    before the versioned evidence contract carries none, and none is
    invented for it: the empty registry and the legacy
    ``quality_contract_version`` are what a consumer refuses strict
    acceptance on.
    """
    evidence_units: dict[str, EvidenceUnit] = Field(default_factory=dict)
    """Every exact passage admitted as evidence, keyed by ``evidence_id``."""
    evidence_dispositions: list[EvidenceDisposition] = Field(default_factory=list)
    """Why each non-admitted item was not admitted; never silently dropped."""
    boundary_audits: dict[str, BoundaryAudit] = Field(default_factory=dict)
    """Section 2.6 boundary manifests, keyed by ``audit_id``."""
    quality_contract_version: str = LEGACY_QUALITY_CONTRACT_VERSION
    """Which evidence/quality contract wrote this snapshot.

    ``LEGACY_QUALITY_CONTRACT_VERSION`` for every snapshot written before the
    versioned contract existed — the honest value, because such a snapshot
    cannot prove its reads. New runs stamp ``QUALITY_CONTRACT_VERSION``.
    """
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
    read_records: dict[str, ReadRecord]
    evidence_units: dict[str, EvidenceUnit]
    evidence_dispositions: list[EvidenceDisposition]
    boundary_audits: dict[str, BoundaryAudit]
    quality_contract_version: str
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

# Registries whose update folds into what the state already holds, through the
# conflict-detecting reducers in ``agents.evidence``. An update supplies only
# the records it produced; the reducer keeps the rest, and one ID carrying two
# different bodies raises instead of overwriting. Imported at call time
# because ``agents.evidence`` imports this module.
_MERGED_STATE_FIELDS = frozenset(
    {
        "read_records",
        "evidence_units",
        "evidence_dispositions",
        "boundary_audits",
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

    from deep_research.agents.evidence import (  # noqa: PLC0415
        merge_boundary_audits,
        merge_evidence_dispositions,
        merge_evidence_units,
        merge_read_records,
    )

    reducers = {
        "read_records": merge_read_records,
        "evidence_units": merge_evidence_units,
        "evidence_dispositions": merge_evidence_dispositions,
        "boundary_audits": merge_boundary_audits,
    }

    payload = state.model_dump(mode="python")
    for field_name, value in update.items():
        if field_name in _APPEND_STATE_FIELDS:
            if not isinstance(value, list):
                raise TypeError(f"{field_name} update must be a list")
            payload[field_name] = [*payload[field_name], *deepcopy(value)]
        elif field_name in _MERGED_STATE_FIELDS:
            # Folded from the state's own records rather than from its dump:
            # the dump has already turned them into plain mappings, and the
            # reducers compare record fields. The result is deep-copied so the
            # merged state shares nothing mutable with the state it came from.
            merged = reducers[field_name](getattr(state, field_name), value)
            payload[field_name] = deepcopy(merged)
        else:
            payload[field_name] = deepcopy(value)

    return ResearchState.model_validate(payload)


def advance_research_iteration(state: ResearchState) -> ResearchState:
    if state.iteration >= state.max_iterations:
        raise ValueError("cannot advance iteration beyond max_iterations")

    payload = state.model_dump(mode="python")
    payload["iteration"] = state.iteration + 1
    return ResearchState.model_validate(payload)
