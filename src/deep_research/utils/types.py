"""Shared typed contracts for research state and domain data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from typing import Annotated, Literal, TypeAlias, TypedDict
from urllib.parse import urlsplit, urlunsplit

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
# How a source's bytes reached this run. A transport relation is not a
# quality dimension: a mirror is the work it mirrors, served from somewhere
# else, so it inherits that work's issuer and contributes no second origin.
TransportRelation: TypeAlias = Literal[
    "original",
    "mirror",
    "syndication",
    "unknown",
]
# What kind of document this is. ``mixed`` is the honest label for one
# article that repeats someone else's statistic *and* adds its own reporting,
# which is why the source level may not decide its dependence.
SourceRole: TypeAlias = Literal[
    "original_report",
    "independent_research",
    "derivative",
    "company_statement",
    "mixed",
    "unknown",
]
# Whether the publisher stands to gain from the claim it makes. ``evidenced``
# is a stated stake (a company's statement about its own product), which
# establishes what the company said and never the truth of it.
SelfInterest: TypeAlias = Literal["none", "potential", "evidenced", "unknown"]
# Which of the source's dates the recency judgement rests on. ``effective``
# is an old rule that still governs; ``stale_data`` is a new publication
# repeating old figures; ``projection`` is a forecast beyond its data.
FreshnessStatus: TypeAlias = Literal[
    "current",
    "superseded",
    "stale_data",
    "projection",
    "effective",
    "unknown",
]
# How an atom's ``subject`` was derived, which is not the same question as what
# it says. ``absent`` is a fact about the clause — it names no entity at all —
# and two such clauses may agree, which is the permissive case the comparison
# must keep. ``unresolved`` is a fact about this contract: an entity position is
# there and the derivation failed (a relation word precedes the entity, a
# trailing phrase is longer than this contract will read whole, the only
# candidate was a bare unit noun). A failed derivation is the absence of
# evidence, and it must never read as two clauses agreeing on the same entity.
# An empty string means "not recorded" — a proposition built by hand, or written
# before this field existed — and is compared on its subject text alone.
SubjectState: TypeAlias = Literal["", "derived", "absent", "unresolved"]


class ContractModel(BaseModel):
    """Base validation behavior shared by public research contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


# The answer forms one research question can take. The form decides what a
# satisfactory answer looks like — a comparison is not finished by two
# unrelated measurements, and a historical question is not answered by today's
# figures — so it is frozen before planning rather than inferred from
# whatever the plan happened to produce.
AnswerKind: TypeAlias = Literal[
    "constraints",
    "comparison",
    "explanation",
    "factual",
    "historical",
]

# One sub-topic carries at most this many answerable obligations. The bound is
# a feasibility rule, not a storage limit: a topic with eight required targets
# cannot be finished by one research pass, and a plan that asks for it is
# asking for a partially answered report.
MAX_TARGETS_PER_TOPIC = 4


class AnswerContract(ContractModel):
    """What this run owes the reader, frozen before any evidence is gathered.

    Section 2.3 freezes the original question, the scope and as-of date, and
    the answer form. This contract is that freeze: every later stage reads the
    question here rather than from a possibly refined copy, and a coverage
    denominator can only ever grow from these values.

    ``as_of_date`` is stamped from the run's injected clock, never from model
    knowledge or memory — a September 2026 session that treated 2024 as
    "current" produced stale anchors (baseline TR-04). When the question
    itself names a date or period, that date is preserved instead: a question
    about 2021 is answered as of 2021, and the contract says so.

    ``geographic_scope`` is ``"unspecified"`` when the question names none,
    and ``assumptions`` then carries the explicit assumption. An empty
    assumptions list would let a regional sample support a global conclusion
    silently; the field is required, so a caller has to say which it is.
    """

    question: str = Field(min_length=1)
    """The original question, verbatim and frozen."""
    scope_statement: str = Field(min_length=1)
    """One sentence naming the scope, the as-of date, and the answer form."""
    geographic_scope: str = Field(min_length=1)
    as_of_date: str = Field(min_length=1)
    """ISO ``YYYY-MM-DD``: the run clock's date, or the date asked for."""
    evidence_period_requirement: str = Field(min_length=1)
    """What period counts as current for this question.

    Publication date, data period, forecast horizon, effective policy date
    and retrieval date are different things; this names which one the
    question is actually about.
    """
    assumptions: list[str]
    """Every assumption the plan makes, said out loud."""
    answer_kind: AnswerKind
    requested_word_limit: int | None = Field(default=None, ge=1)
    """The reader length the question asked for, or ``None`` for the default."""


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
    evidence_targets: list[EvidenceTarget] = Field(
        default_factory=list,
        max_length=MAX_TARGETS_PER_TOPIC,
    )
    """The answerable obligations this sub-topic must satisfy, 1-4 of them.

    Stamped locally by ``PlannerAgent``: the draft proposes obligations, and
    the planner assigns their ids, their required dimensions, and their
    support policy before any verdict exists. An empty list is a *legacy*
    plan — a snapshot written before this contract — and never means "nothing
    is required": such a plan has to be replanned before it can be executed,
    which is what ``planner.targets_requiring_replanning`` reports. The
    ceiling of four keeps one sub-topic from becoming a batch no pass can
    finish; a fifth obligation belongs to its own sub-topic.
    """


AcquisitionStatus: TypeAlias = Literal[
    "queued", "read", "denied", "unusable", "deferred"
]
CandidateDiscovery: TypeAlias = Literal["search", "memory", "document_link"]


def _canonical_acquisition_url(value: str) -> str:
    """Canonicalize a candidate URL without importing the agent layer."""
    collapsed = " ".join(value.split())
    try:
        parts = urlsplit(collapsed)
        if not parts.scheme or not parts.hostname:
            return collapsed
        scheme = parts.scheme.casefold()
        host = parts.hostname.casefold()
        if host.startswith("www."):
            host = host[4:]
        netloc = host
        port = parts.port
        if port is not None and not (
            (scheme == "http" and port == 80)
            or (scheme == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"
        return urlunsplit(
            (scheme, netloc, parts.path.rstrip("/"), parts.query, "")
        )
    except ValueError:
        return collapsed


class CandidateRecord(ContractModel):
    """One queued source lead and its explicit acquisition disposition."""

    candidate_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str = ""
    target_ids: list[str] = Field(default_factory=list)
    selection_reason: str = Field(
        default="candidate discovered for the active target", min_length=1
    )
    discovered_via: CandidateDiscovery
    status: AcquisitionStatus = "queued"
    read_id: str | None = None

    @model_validator(mode="after")
    def normalize_identity(self) -> "CandidateRecord":
        self.url = _canonical_acquisition_url(self.url)
        if not self.url:
            raise ValueError("candidate URL must not be blank")
        self.title = " ".join(self.title.split())
        self.target_ids = list(dict.fromkeys(self.target_ids))
        return self


class AcquisitionState(ContractModel):
    """Deterministic, persisted state for one target's acquisition pass."""

    candidate_urls: list[str] = Field(default_factory=list)
    attempted_urls: list[str] = Field(default_factory=list)
    read_urls: list[str] = Field(default_factory=list)
    denied_urls: list[str] = Field(default_factory=list)
    pending_passage_ids: list[str] = Field(default_factory=list)
    pending_extraction_ids: list[str] = Field(default_factory=list)
    target_id: str | None = None
    remaining_calls: int = Field(default=0, ge=0)
    consecutive_searches: int = Field(default=0, ge=0)
    empty_searches: int = Field(default=0, ge=0)
    candidate_records: dict[str, CandidateRecord] = Field(default_factory=dict)
    remaining_model_turns: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def normalize_queue(self) -> "AcquisitionState":
        for field_name in (
            "candidate_urls",
            "attempted_urls",
            "read_urls",
            "denied_urls",
        ):
            values = getattr(self, field_name)
            normalized: list[str] = []
            for value in values:
                candidate = _canonical_acquisition_url(value)
                if candidate and candidate not in normalized:
                    normalized.append(candidate)
            setattr(self, field_name, normalized)

        records: dict[str, CandidateRecord] = {}
        for key, record in self.candidate_records.items():
            normalized = _canonical_acquisition_url(record.url or key)
            if not normalized:
                raise ValueError("candidate record URL must not be blank")
            if normalized in records and records[normalized] != record:
                raise ValueError(
                    f"candidate records collide at canonical URL {normalized!r}"
                )
            if record.url != normalized:
                record = record.model_copy(update={"url": normalized})
            records[normalized] = record
        self.candidate_records = records
        for field_name in ("pending_passage_ids", "pending_extraction_ids"):
            setattr(self, field_name, list(dict.fromkeys(getattr(self, field_name))))
        if self.target_id is not None:
            self.target_id = self.target_id.strip() or None
        return self


class Finding(ContractModel):
    content: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    extracted_at: AwareISOString
    confidence: UnitScore
    related_sub_topic: str = Field(min_length=1)


class SourceTemporal(ContractModel):
    """The dates one source carries, kept apart because they mean different things.

    Section 2.3: a publication date, the period the data actually covers, the
    horizon a projection refers to, and the date a rule took effect are four
    different facts. Collapsing them is how a 2035 projection becomes today's
    cost and how a newly published article repeating 2019 figures reads as
    current, so each is recorded separately and ``status`` names which one the
    recency judgement rested on.
    """

    publication_date: str | None = None
    data_period: str | None = None
    """The observation period the source's data cover, which is not its date."""
    forecast_horizon: str | None = None
    """The future period a projection refers to, which is not an observation."""
    effective_date: str | None = None
    """When a rule, standard, or version took effect."""
    status: FreshnessStatus = "unknown"
    """``unknown`` whenever the read cannot support the claimed status."""


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
    methods_score: UnitScore | None = None
    """How well the source documents how its result was produced.

    Recorded beside the three blended dimensions rather than folded into
    ``overall_score``: the convex combination is a frozen contract, and a
    well-documented method on an off-target source must not rescue it.
    """
    serving_host: str | None = None
    """The host the bytes were served from — a transport fact, not a publisher."""
    publisher_id: str | None = None
    """The publisher the read evidences, or the serving host's identity.

    ``None`` whenever identity is not established — an unresolved issuer on an
    opaque URL, or a partial read, which is never an identity edge. Consumers
    must read ``None`` as "cannot establish this publisher", never as "a new
    one".
    """
    work_id: str | None = None
    """The intellectual work this source is a copy of, or ``None``."""
    transport_relation: TransportRelation = "unknown"
    source_role: SourceRole = "unknown"
    self_interest: SelfInterest = "unknown"
    temporal: SourceTemporal = Field(default_factory=SourceTemporal)
    cited_sub_topics: list[str] = Field(default_factory=list)
    """The sub-topics this source was cited for — its target relevance."""
    target_ids: list[str] = Field(default_factory=list)
    """The evidence targets the read behind this source was associated with."""
    assessment_revision: str = ""
    """The content/metadata/temporal revision this assessment was made from.

    Empty means no revision was recorded, which is the honest value for a
    record built without a read. Task 5's reverification cache key includes
    this field, so a changed body, title, or dating signal invalidates a
    cached verdict instead of surviving as a stale judgement.
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
        elif any(score is not None for score in scores) or (
            self.methods_score is not None
        ):
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

# What ``ReadRecord.content_sha256`` carries when the reader could not hash a
# complete document — a PDF that lost a page, say. It is deliberately not a
# digest: a hash over part of a document would identify a work nobody fully
# read, and two different partial extractions would share one identity. The
# readers publish this same marker (``tools.document_reader``), so the tool
# boundary and the persisted record cannot disagree about it.
INCOMPLETE_CONTENT_SHA256 = "incomplete"

_HEX_DIGITS = frozenset("0123456789abcdef")


def _is_content_digest(value: str) -> bool:
    """True for a full lowercase SHA-256 digest, and for nothing else."""
    return len(value) == 64 and set(value) <= _HEX_DIGITS


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

    A read whose extraction was incomplete is still a read: it keeps every
    locator it did extract, so a 200-page document that lost page 7 stays
    auditable instead of disappearing from the registry. What it may not keep
    is a content identity, so ``content_sha256`` carries
    ``INCOMPLETE_CONTENT_SHA256`` and the two fields are checked against each
    other below.
    """

    read_id: str = Field(min_length=1)
    requested_url: str = Field(min_length=1)
    resolved_url: str = Field(min_length=1)
    """The URL the content was served from, after any redirect."""
    title: str = Field(min_length=1)
    reader: Literal["web_scraper", "document_reader"]
    retrieved_at: AwareISOString
    """When this body was observed — preserved across cache admission."""
    content_sha256: str = Field(min_length=1)
    """The digest of the complete text, or the explicit incomplete marker."""
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

    @model_validator(mode="after")
    def validate_content_identity(self) -> ReadRecord:
        """A content hash and a completeness claim must agree.

        A complete read publishes the digest of its whole document. A read
        whose extraction was incomplete publishes the marker instead, because
        a digest over the part that happened to parse would identify a work
        nobody fully read. Neither direction may be smuggled past this check.
        """
        if self.extraction_complete:
            if not _is_content_digest(self.content_sha256):
                raise ValueError(
                    "a complete read requires the SHA-256 of its full text"
                )
        elif self.content_sha256 != INCOMPLETE_CONTENT_SHA256:
            raise ValueError(
                "an incomplete extraction must not publish a content hash"
            )
        return self


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


# The reserved ``EvidenceTarget.question`` that records an original-question
# omission. A target carrying it says "the question asks for something this
# plan does not yet cover"; it is a marker for a reviewed omission, not an
# evidence obligation, so ``counted_evidence_targets`` leaves it out of every
# target count. Counting it would let a plan look complete while the omission
# it stands for is still open.
ORIGINAL_QUESTION_OMISSION_REFERENCE = (
    "original question omission: not yet a counted evidence target"
)


def counted_evidence_targets(
    targets: Sequence[EvidenceTarget],
) -> list[EvidenceTarget]:
    """The targets that count toward coverage, in their given order.

    The one place the reserved omission reference is filtered, so a consumer
    cannot accidentally count it by iterating the list itself.
    """
    return [
        target
        for target in targets
        if target.question != ORIGINAL_QUESTION_OMISSION_REFERENCE
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


class ConflictAssessment(ContractModel):
    """What was done about two candidates that disagree about one claim.

    A conflict is a fact about the evidence, so it is recorded rather than
    resolved away: a missing row means the conflict was never assessed, which is
    *unresolved*, never an implicit dismissal. ``resolution`` is the local
    classification — ``resolved`` only when the disagreement is accounted for by
    a scope, method, or period difference this contract can see,
    ``not_comparable`` when the two passages are about different things
    entirely, and ``unresolved`` otherwise. A material unresolved contradiction
    precludes settled ``verified`` wording.
    """

    claim_cluster_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    same_scope: bool = False
    material: bool = True
    resolution: Literal["resolved", "unresolved", "not_comparable"] = "unresolved"
    rationale: str = Field(min_length=1)


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
    # The evidence targets whose obligation this claim discharges. Task 2
    # stamped a target id and its required dimensions on every planned
    # obligation, so this is the typed link the claim scheduler reads: a batch
    # takes one outstanding obligation per target before it takes extra
    # low-value claims, and a claim that names no target can only ever fill an
    # extra slot.
    target_ids: list[str] = Field(default_factory=list)
    # The stable identity of the atomic proposition this claim states. Minted
    # once from the cluster's first canonical anchor and persisted through
    # every refinement, so a restatement of one fact is one identity and never
    # a second row in the ledger. ``cluster_aliases`` carries the identities a
    # merge absorbed, so a consumer holding either id resolves to the same
    # cluster.
    cluster_id: str | None = None
    cluster_aliases: list[str] = Field(default_factory=list)
    # Section 2.1's badge, stated separately from the legacy verdict so
    # "independent corroboration established" and "primary-source attribution"
    # can never be read as the same thing. ``verified_pair`` is written only
    # when two selected passages each completely support the claim from
    # different known publishers, works, and claim-specific origins, with no
    # unresolved material contradiction; ``source_supported`` is the strictly
    # weaker "a source said this", which keeps the legacy
    # ``insufficient_evidence`` verdict beside it. ``None`` for a claim judged
    # before this contract existed, or with no evidence to classify at all.
    evidence_status: str | None = None
    # Every conflict this claim's adjudication had to face, with what was done
    # about it. Empty means none was recorded, which is not the same as "none
    # existed" — a consumer that needs that distinction reads the boundary
    # manifest, not this list.
    conflict_assessments: list[ConflictAssessment] = Field(default_factory=list)
    # Every local audit flag this adjudication raised, from the enumerated
    # vocabulary: the flags may coexist, and recording only the first would
    # make a claim look diagnosable when three conditions apply. The tokens are
    # plain strings for the same reason as ``insufficient_reason`` — a snapshot
    # written by a later release must stay readable.
    audit_flags: list[str] = Field(default_factory=list)


class AtomicProposition(ContractModel):
    """One checkable assertion, with every qualifier that changes its meaning.

    A claim's prose can carry several assertions, and two claims can state one
    assertion in two ways. This is the unit both are reduced to: the clause
    itself, plus the dimensions that decide whether two clauses assert the
    same thing. An empty dimension means the clause does not state it, which
    is deliberately not the same as "states none" — a proposition that states
    nothing about geography is not evidence that it applies everywhere, only
    that this contract cannot tell.
    """

    text: str = Field(min_length=1)
    atom_id: str = ""
    """The stable identity of this atom within the claim it was split from.

    A compound claim becomes several atoms, and each has to stay addressable
    on its own: without an id, two atoms of one claim publish as two rows with
    the same compound text and the same claim id, which is the ledger defect
    atomizing exists to remove.
    """
    subject: str = ""
    """The entity the assertion is about, as a noun phrase, or empty."""
    subject_state: SubjectState = ""
    """How ``subject`` was derived: ``derived``, ``absent``, or ``unresolved``.

    ``absent`` and ``unresolved`` are different claims about a clause, and
    reading the second as the first is what let two clauses with different
    entities agree on an empty string. See :data:`SubjectState`.
    """
    predicate: str = ""
    """The *relation class* the assertion states about its subject.

    Not the surface verb: one assertion is written "held", another "sat", a
    third "reported", and all three state the same level. The field holds what
    is comparable — ``states_value``, ``increased``, ``decreased``,
    ``withholds``, ``projected`` and so on — because its job is to be
    compared, while the clause's own wording stays in ``text``.
    """
    value: str = ""
    unit: str = ""
    observation_period: str = ""
    """The period the assertion's data cover, which is not its publication."""
    geography: str = ""
    population: str = ""
    """The measured population, when the assertion quantifies over one."""
    quantity_noun: str = ""
    """The noun a measured quantity is of: "10 GW of capacity" names capacity.

    A quantity phrase's ``of``-noun is not the assertion's entity — it says what
    was measured, not what the clause asserts about — so it is read separately.
    It is compared only when both clauses name one, because the same assertion
    may state it or leave it implied ("10 GW of capacity was added to the Texas
    grid" and "10 GW was added to the Texas grid"); an unnamed measurand is not
    a different one, while two named and different ones are two quantities.
    """
    denominator: str = ""
    """The base a share is taken of — a percentage without one is ambiguous."""
    attribution: str = ""
    forecast_status: str = ""
    """``observed``, ``projected``, ``forecast``, ``estimated``, or empty."""
    negated: bool = False
    parent_claim_id: str = ""
    """The claim whose prose this atom was split out of."""
    member_claim_ids: list[str] = Field(default_factory=list)
    """Every claim whose prose asserts this atom, so both stay addressable."""
    evidence_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)


class ClaimCluster(ContractModel):
    """One atomic proposition, its stable identity, and everything behind it.

    ``cluster_id`` is minted once from the cluster's first canonical anchor and
    is never recomputed from its members: refinement adds evidence and member
    claims, and rehashing the growing member set would mint a new identity on
    every pass. ``proposition`` is that same first anchor, so the cluster's own
    wording does not drift as paraphrases join it.

    The provenance a later pass needs is *persisted here*, not re-derived: a
    refinement that submits only the new claim has to reconstruct the
    citations, passages, verdicts, and consumed identities of everything
    already in the cluster, and the cluster itself is the only place that has
    them.
    """

    cluster_id: str = Field(min_length=1)
    proposition: AtomicProposition
    created_seq: int = Field(default=0, ge=0)
    """When this cluster's identity was first minted, as a persisted counter.

    The oldest cluster is the one that survives a merge, so age cannot be
    inferred from the order a caller happens to pass clusters in. A cluster
    written before this field existed, or built by hand, carries zero, which
    ``claim_clusters.oldest_first`` orders *last* — never first — and peers at
    one sequence are ordered by cluster id rather than by registry position, so
    no caller's list order can hand one of two equal clusters the surviving
    identity.
    """
    evidence_ids: list[str] = Field(default_factory=list)
    member_claim_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    cluster_aliases: list[str] = Field(default_factory=list)
    """The cluster ids a merge absorbed, so either id resolves here."""
    source_urls: list[str] = Field(default_factory=list)
    """Every citation URL behind this cluster, unioned across its members."""
    verification_evidence: list[EvidencePassage] = Field(default_factory=list)
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    """Every verdict recorded for this proposition, sorted and distinct.

    A set rather than one value because members of one cluster can disagree,
    and a disagreement may never read as a settled fact.
    """
    verdict_evidence: dict[str, list[str]] = Field(default_factory=dict)
    """Each verdict mapped to the citation URLs that actually recorded it.

    Kept per verdict rather than as one unioned set so a reader can see which
    sources stood behind the contradiction and which behind the verification;
    a diagnostic that names every citation for every verdict says nothing
    about which evidence supported which judgement.
    """
    confidence: float | None = None
    """The lowest confidence any member recorded — the conservative one."""
    insufficient_reason: str | None = None
    consumed_finding_fingerprints: list[str] = Field(default_factory=list)
    consumed_coverage_ids: list[str] = Field(default_factory=list)
    status: Literal[
        "canonical", "duplicate_representative", "contested"
    ] = "canonical"
    """``duplicate_representative`` when only one of two near-duplicates may
    publish. ``contested`` when its members were adjudicated differently: such
    a cluster can never be a settled supporting fact, and it cannot count as
    independent corroboration for anything else."""
    diagnostics: list[str] = Field(default_factory=list)


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
    answer_contract: AnswerContract | None = None
    """The frozen scope, as-of date, and answer form for this session.

    ``None`` until a plan has been produced. Set once by the Planner and
    replaced only by a later planning pass that freezes the *same* original
    question; nothing downstream may rewrite ``question``, ``as_of_date``, or
    ``geographic_scope`` to make a report look complete.
    """
    sub_topics: list[SubTopic] = Field(default_factory=list)
    initial_target_ids: list[str] = Field(default_factory=list)
    """Every evidence target the first plan stamped, in plan order.

    Appendix-only: a later planning pass may add to this list and can never
    remove from it. ``merge_research_state`` unions these ids rather than
    replacing them, so a refinement that names three of five original targets
    cannot shrink the coverage denominator (Section 2.3).
    """
    expanded_target_ids: list[str] = Field(default_factory=list)
    """Targets a later reviewed ``extend_plan`` pass added to the inventory.

    Kept apart from ``initial_target_ids`` so a reviewer can tell what the
    first plan owed from what later omissions added; the effective inventory
    is the union of both, never a smaller denominator.
    """
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
    claim_clusters: dict[str, ClaimCluster] = Field(default_factory=dict)
    """Every atomic claim cluster this session has minted, by ``cluster_id``.

    Carried in state because a refinement is a *different invocation*: the
    cluster holds the citations, passages, verdicts, and consumed identities
    its members accumulated, and a later pass that resubmits only one new
    claim can reconstruct the rest from this registry and nothing else. A
    snapshot written before the registry existed carries none, and none is
    invented for it — the empty registry is the honest value.
    """
    evidence_dispositions: list[EvidenceDisposition] = Field(default_factory=list)
    """Why each non-admitted item was not admitted; never silently dropped."""
    boundary_audits: dict[str, BoundaryAudit] = Field(default_factory=dict)
    """Section 2.6 boundary manifests, keyed by ``audit_id``."""
    acquisition_state_by_target: dict[str, AcquisitionState] = Field(
        default_factory=dict
    )
    """The explicit acquisition queue and policy state for each target."""
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
    answer_contract: AnswerContract | None
    sub_topics: list[SubTopic]
    initial_target_ids: list[str]
    expanded_target_ids: list[str]
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
    claim_clusters: dict[str, ClaimCluster]
    evidence_dispositions: list[EvidenceDisposition]
    boundary_audits: dict[str, BoundaryAudit]
    acquisition_state_by_target: dict[str, AcquisitionState]
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

# Fields whose update adds ids to what the state already holds, in
# first-seen order, and can never remove one. These are the target
# inventories: Section 2.3 lets a later reviewed omission ADD a target and
# never drop or weaken one, and an update that names a subset of the existing
# ids is exactly how a smaller denominator would otherwise appear. Kept
# distinct from ``_APPEND_STATE_FIELDS`` because a repeated id must not be
# stored twice here — the inventory is a set with a stable order, not a log.
_UNION_STATE_FIELDS = frozenset(
    {
        "initial_target_ids",
        "expanded_target_ids",
    }
)


def _union_ids(existing: Sequence[str], added: Sequence[str]) -> list[str]:
    """``existing`` order first, then every id ``added`` brings that is new."""
    merged = list(existing)
    for item in added:
        if item not in merged:
            merged.append(item)
    return merged


_CANDIDATE_STATUS_PRIORITY: dict[AcquisitionStatus, int] = {
    "queued": 0,
    "deferred": 1,
    "unusable": 2,
    "denied": 3,
    "read": 4,
}


def _merge_candidate_records(
    previous: Mapping[str, CandidateRecord],
    current: Mapping[str, CandidateRecord],
) -> dict[str, CandidateRecord]:
    """Merge candidate identities while allowing monotonic state changes."""
    merged = dict(previous)
    for key, incoming in current.items():
        canonical = _canonical_acquisition_url(key)
        existing = merged.get(canonical)
        if existing is None:
            merged[canonical] = incoming
            continue
        if existing.candidate_id != incoming.candidate_id:
            raise ValueError(
                f"candidate {canonical!r} already has another candidate id"
            )
        if (
            existing.url != incoming.url
            or existing.discovered_via != incoming.discovered_via
        ):
            raise ValueError(
                f"candidate {canonical!r} already has a different identity"
            )
        # A changed source version is a new read identity for the same queued
        # URL. Keep both read records; the latest read association wins rather
        # than treating a version change as an identity corruption.
        targets = _union_ids(existing.target_ids, incoming.target_ids)
        selected_reason = existing.selection_reason
        if not selected_reason.strip() and incoming.selection_reason.strip():
            selected_reason = incoming.selection_reason
        status = incoming.status
        if _CANDIDATE_STATUS_PRIORITY[existing.status] > _CANDIDATE_STATUS_PRIORITY[
            incoming.status
        ]:
            status = existing.status
        merged[canonical] = existing.model_copy(
            update={
                "title": existing.title or incoming.title,
                "target_ids": targets,
                "selection_reason": selected_reason,
                "status": status,
                "read_id": (
                    incoming.read_id
                    if incoming.status == "read" and incoming.read_id
                    else existing.read_id or incoming.read_id
                ),
            }
        )
    return merged


def _merge_acquisition_state(
    previous: AcquisitionState,
    incoming: AcquisitionState,
) -> AcquisitionState:
    """Fold one target's updates without resurrecting spent capacity."""
    records = _merge_candidate_records(
        previous.candidate_records, incoming.candidate_records
    )
    queue_candidates = _union_ids(
        previous.candidate_urls, incoming.candidate_urls
    )
    queue = [
        url
        for url in queue_candidates
        if url not in records or records[url].status == "queued"
    ]
    return AcquisitionState(
        candidate_urls=queue,
        attempted_urls=_union_ids(
            previous.attempted_urls, incoming.attempted_urls
        ),
        read_urls=_union_ids(previous.read_urls, incoming.read_urls),
        denied_urls=_union_ids(previous.denied_urls, incoming.denied_urls),
        pending_passage_ids=list(incoming.pending_passage_ids),
        pending_extraction_ids=list(incoming.pending_extraction_ids),
        target_id=incoming.target_id or previous.target_id,
        remaining_calls=min(previous.remaining_calls, incoming.remaining_calls),
        consecutive_searches=incoming.consecutive_searches,
        empty_searches=incoming.empty_searches,
        candidate_records=records,
        remaining_model_turns=min(
            previous.remaining_model_turns, incoming.remaining_model_turns
        ),
    )


def merge_acquisition_states(
    previous: Mapping[str, AcquisitionState],
    current: Mapping[str, AcquisitionState],
) -> dict[str, AcquisitionState]:
    """ID-aware reducer for the per-target acquisition registry."""
    merged = dict(previous)
    for target_id, incoming in current.items():
        key = target_id.strip()
        if not key:
            raise ValueError("acquisition state keys must not be blank")
        if incoming.target_id is not None and incoming.target_id != key:
            raise ValueError(
                f"acquisition state {key!r} carries another target id"
            )
        normalized = incoming if incoming.target_id == key else incoming.model_copy(
            update={"target_id": key}
        )
        existing = merged.get(key)
        merged[key] = (
            normalized
            if existing is None
            else _merge_acquisition_state(existing, normalized)
        )
    return merged


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

    from deep_research.agents.claim_clusters import (  # noqa: PLC0415
        merge_claim_cluster_registry,
    )
    from deep_research.agents.evidence import (  # noqa: PLC0415
        merge_boundary_audits,
        merge_evidence_dispositions,
        merge_evidence_units,
        merge_read_records,
    )

    # The one list of registries whose update folds into what the state already
    # holds, through the conflict-detecting reducers in ``agents.evidence``: an
    # update supplies only the records it produced, the reducer keeps the rest,
    # and one ID carrying two different bodies raises instead of overwriting.
    # A name that is not a key here is not merged — so a registry added to the
    # state without a reducer fails the unknown-field check or replaces
    # loudly, and can never quietly become last-write-wins. Imported at call
    # time because ``agents.evidence`` imports this module.
    reducers = {
        "read_records": merge_read_records,
        "evidence_units": merge_evidence_units,
        "evidence_dispositions": merge_evidence_dispositions,
        "boundary_audits": merge_boundary_audits,
        "acquisition_state_by_target": merge_acquisition_states,
        "claim_clusters": merge_claim_cluster_registry,
    }

    payload = state.model_dump(mode="python")
    for field_name, value in update.items():
        if field_name in _UNION_STATE_FIELDS:
            if not isinstance(value, list):
                raise TypeError(f"{field_name} update must be a list")
            payload[field_name] = _union_ids(payload[field_name], value)
        elif field_name in _APPEND_STATE_FIELDS:
            if not isinstance(value, list):
                raise TypeError(f"{field_name} update must be a list")
            payload[field_name] = [*payload[field_name], *deepcopy(value)]
        elif field_name in reducers:
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
