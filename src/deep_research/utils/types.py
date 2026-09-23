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
    target_ids: list[str] = Field(default_factory=list)
    """The planned evidence targets this finding's content answers.

    The extraction names them from the plan's own inventory (``topic-01-
    target-02``), which is a different vocabulary from the coverage ids the
    acquisition layer stamps on reads and evidence units (``topic-01``): a
    read is *fetched for* a sub-topic, while a finding *answers* an
    obligation, and one read can answer a target of a topic that never
    fetched it. That is exactly the binding an audited run discarded — it
    asked the model for "the target id it serves" and then kept only
    ``related_sub_topic`` — which left every claim unbound and every target
    unanswered. Empty is a legacy finding, extracted before the binding was
    kept; a consumer then falls back to the finding's sub-topic, never to
    "answers everything".
    """
    vintage: str | None = None
    """The dated edition of the data the figure rests on, in the source's words.

    "January 2025 preliminary electric generator inventory" is a vintage;
    "2024" is a period. Two statements of one quantity can differ only by
    this, so a later stage comparing "the latest" needs it recorded beside
    the figure rather than inside its prose.
    """
    statement_date: str | None = None
    """The date the source states the figure or carries it, as it writes it.

    The article's own date — "March 12, 2025" — which is not the vintage of
    its data and not the period the figure applies to. It is what tells a
    February 2025 forecast of 18.2 GW from a March 2025 statement of 19.6 GW.
    """
    data_period: str | None = None
    """The period the figure applies to, as the source writes it.

    "2024" for an addition, "2025" for a forecast about that year: the field
    that says whether two figures are even comparable, so a vintage
    comparison never ranks a 2024 actual against a 2025 projection.
    """

    @model_validator(mode="after")
    def normalize_binding_and_dates(self) -> "Finding":
        """Keep first-seen target order, and treat a blank date as absent.

        Both are what a consumer compares against: a repeated target id would
        make one binding look like two, and an empty string would sort as a
        date a reader could rank against a real one.
        """
        self.target_ids = list(dict.fromkeys(self.target_ids))
        for name in ("vintage", "statement_date", "data_period"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                setattr(self, name, None)
        return self


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
    """The intellectual work this source is a copy of, or ``None``.

    Always ``work_identity.key`` once identity is resolved: kept as its own
    field because every consumer that only needs the key reads it here.
    """
    identity_anchors: dict[str, str | list[str]] = Field(default_factory=dict)
    """The metadata anchors the read was shown to evidence, and nothing else.

    ``issuer``/``doi``/``year``/``report_number`` as validated against the
    read, plus ``derived_from`` — the works the document says its data come
    from, as the ids a lineage comparison reads (``doi:<doi>``, or
    ``report-number:<number>`` for a cited number, whose issuer namespace
    belongs to the cited document). Persisted so work identity can be
    re-resolved across every read of a run without asking the model again.
    """
    work_identity: WorkIdentity | None = None
    """The work this source belongs to, resolved across every read of the run.

    ``None`` for a record with no read behind it, or one written before batch
    resolution existed. Carries the aliases, basis, status, and evidenced
    derivation that ``work_id`` alone cannot.
    """
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
    a scope, method, or period difference this contract can see, and
    ``unresolved`` otherwise. ``not_comparable`` is legacy and read-only: no
    code path produces it today, but a stored row carrying it still loads and
    stays non-blocking, exactly as it did before. A material unresolved
    contradiction precludes settled ``verified`` wording.
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
    # ``insufficient_evidence`` verdict beside it; ``contested`` is both sides
    # present with the disagreement unresolved. ``None`` for a claim judged
    # with no evidence to classify at all.
    #
    # Enumerated rather than a plain ``str``: ``verified`` may only be
    # published with ``verified_pair`` behind it, and a value this contract
    # does not know must fail loudly rather than read as "some other badge".
    evidence_status: (
        Literal["verified_pair", "source_supported", "contested"] | None
    ) = None
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
    # The exact evidence ids behind ``verification_evidence``, keyed by the
    # stance each was selected with. Section 2.4's boundary manifest has to be
    # joinable end to end, and a passage can be joined by its id but never by
    # its excerpt: two units of a mirror pair carry identical text, so an
    # excerpt join reports both of them for one selection.
    evidence_selection: dict[str, str] = Field(default_factory=dict)
    # Selections local validation refused — an id that was not in the packet, a
    # stance that contradicted the list it was selected in, a duplicate. Without
    # these the manifest cannot tell "not selected" from "selected and refused".
    refused_evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verified_is_backed_by_a_pair(self) -> Claim:
        """``verified`` is only ever published with the strict pair behind it.

        The badge is not decoration: the legacy passage path had no pair test
        at all, so a single unpersisted read could settle a claim. Enforcing
        the pairing here makes that impossible for every producer — the packet
        adjudication, the cluster snapshot, and any caller that builds a
        ``Claim`` by hand — rather than leaving it to each of them to remember.
        """
        if self.verdict == "verified" and self.evidence_status != "verified_pair":
            raise ValueError(
                "verified requires evidence_status='verified_pair': the badge "
                "is written only by the strict independent-pair test"
            )
        return self


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
    comparator: str = ""
    """The bound or approximation the clause puts on its value, or empty.

    "more than 10 GW", "up to 10 GW", and "nearly 10 GW" are three different
    assertions about a number: two are bounds in opposite directions and one is
    an approximation. Comparing only the number treats all of them as "10 GW".
    """
    change_kind: str = ""
    """Whether the stated value is a level or a change: ``level`` or ``delta``.

    "rose to 10 GW" says the subject reached 10 GW; "rose by 10 GW" says it
    gained that much. Both write the same verb and the same number, and only
    the preposition says which one is meant.
    """
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
    verdict_evidence_status: dict[str, str] = Field(default_factory=dict)
    """Each recorded verdict mapped to the evidence badge that carried it.

    The badge is not derivable from the verdict alone — ``verified`` means
    "independent corroboration established" only when some member actually
    recorded ``verified_pair`` — so it is persisted beside the verdicts instead
    of being assumed by whoever reads the cluster back. A verdict with no
    recorded badge resolves to no badge, and the canonical snapshot may then
    not publish ``verified``.
    """


class SubstantiveCoverage(ContractModel):
    """Section 2.3 coverage, read from answers rather than from claim counts.

    A target is answered only when a reader statement names it, asserts
    something in a substantive mode, fills every required dimension, and rests
    on evidence that carries the target's own support policy — that is
    ``target_is_answered``, applied here to every counted required target in
    the plan. A topic is covered only when it has counted obligations and every
    one of them is answered: a topic that owes nothing has demonstrated
    nothing, which is exactly the legacy plan that has to be replanned, and
    calling it covered is the vacuous 100% this contract exists to prevent.

    Target and topic metrics are reported apart because they fail differently:
    nine tenths of the targets can be answered while one critical topic is
    untouched, and a single blended ratio hides exactly that.
    """

    planned_topics: int = Field(ge=0)
    covered_topics: int = Field(ge=0)
    topic_ratio: UnitScore
    planned_targets: int = Field(ge=0)
    required_targets: int = Field(ge=0)
    answered_targets: int = Field(ge=0)
    critical_targets: int = Field(ge=0)
    answered_critical_targets: int = Field(ge=0)
    unanswered_required_target_ids: list[str] = Field(default_factory=list)
    unanswered_critical_target_ids: list[str] = Field(default_factory=list)
    accounted_target_ids: list[str] = Field(default_factory=list)
    """Unanswered required targets with a recorded reason on the audit trail."""
    unaccounted_target_ids: list[str] = Field(default_factory=list)
    """Unanswered required targets with no recorded reason at all."""
    initial_target_ids: list[str] = Field(default_factory=list)
    expanded_target_ids: list[str] = Field(default_factory=list)
    """The frozen inventory, kept so a denominator can only ever grow."""


class ReportQualitySnapshot(ContractModel):
    """Deterministic integrity and coverage metrics for one report pass.

    Every field here is a *structural* diagnostic: a count, a ratio, or an
    enumerated hard-failure name derived from typed state and the composition.
    Nothing in this model is a reader-quality judgement, which is why the
    terminal semantic review is stored on its own (``semantic_review_status``,
    ``semantic_review_score``) rather than folded into ``hard_failures`` or
    into the Critic's own score: a structural clean bill of health and a
    semantic acceptance are two different claims, and the reviewed baseline
    showed a report can hold the first while failing the second.

    ``coverage_ratio`` and ``covered_topics`` are the *claimed* reading — the
    share of planned topics some checked claim recorded consuming. They are
    kept because historical artifacts carry them, and they are never the
    acceptance metric: a claim that consumed a topic id is not an answered
    obligation. ``substantive_topic_ratio`` and ``answered_targets`` are that
    stricter reading (Section 2.3), computed from the target-answer
    assessments plus local evidence-policy validation, over the same
    denominator — the union of the initial and expanded target inventories is
    read from the plan, and no missing record shrinks it.
    """

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
    # --- Task 10: the substantive reading of the same denominator -----------
    substantive_topic_ratio: UnitScore = 0.0
    """Topics whose every counted required target is answered, over the plan."""
    substantive_covered_topics: int | None = Field(default=None, ge=0)
    """The numerator of ``substantive_topic_ratio``, as a count.

    ``covered_topics`` above is the *claimed* reading kept for historical
    artifacts; this is the measured one a reader-facing surface must report.
    They disagree exactly when a topic was claimed but never answered.

    ``None`` on a record written before this field existed — such a snapshot
    already carries ``substantive_topic_ratio`` and the claimed count, and
    publishing a zero numerator beside a nonzero ratio would contradict itself
    in one sentence. ``measured_covered_topics`` is the reading to publish.
    """

    @property
    def measured_covered_topics(self) -> int:
        """The substantive count to publish, or the claimed one on a legacy row."""
        if self.substantive_covered_topics is None:
            return self.covered_topics
        return self.substantive_covered_topics

    planned_targets: int = Field(default=0, ge=0)
    required_targets: int = Field(default=0, ge=0)
    answered_targets: int = Field(default=0, ge=0)
    critical_targets: int = Field(default=0, ge=0)
    unanswered_critical_target_ids: list[str] = Field(default_factory=list)
    unaccounted_target_ids: list[str] = Field(default_factory=list)
    """Required targets with neither an answer nor a recorded reason."""
    # --- Task 10: the semantic judgement, kept apart from the two above -----
    semantic_review_status: str = ""
    """``scored`` / ``incomplete`` / ``provider_failed``, or ``""`` for none.

    Deliberately not a ``hard_failure``: a review that could not be made is an
    absent judgement, and the hard-failure list is a closed set of defects
    found in the report. Deliberately not the Critic's ``review_status``
    either — that one is a terminal signal on a different contract.
    """
    semantic_review_score: float | None = None
    """The review's mean over the seven dimensions, or ``None`` for no score."""
    semantic_review_fingerprint: str = ""
    """The exact packet fingerprint the stored judgement was made over."""


# --- Task 8: the Critic's typed defect vocabulary ---------------------------
#
# Ten kinds and six repair actions, and both sets are *normative*: the kind
# names what is wrong rather than how to fix it, and ``REPAIR_ACTIONS`` is the
# key set of the route table in ``graph.nodes`` (``REPAIR_NODES``), which maps
# each action to the node that performs it. A kind outside this list is a
# schema failure, not a new category — a free-text kind would make "which
# defects does this system find?" unanswerable.
GapKind: TypeAlias = Literal[
    "coverage",
    "missing_support",
    "acquisition",
    "identity",
    "contradiction",
    "semantic_duplicate",
    "source_quality",
    "mechanism",
    "freshness",
    "presentation",
]
GAP_KINDS: tuple[GapKind, ...] = (
    "coverage",
    "missing_support",
    "acquisition",
    "identity",
    "contradiction",
    "semantic_duplicate",
    "source_quality",
    "mechanism",
    "freshness",
    "presentation",
)

# ``critical`` and ``major`` are the material defects: a report cannot be
# accepted while one is open. ``minor`` is a real but editorial observation,
# which is what keeps one wording defect from buying a whole research pass.
GapSeverity: TypeAlias = Literal["critical", "major", "minor"]
GAP_SEVERITIES: tuple[GapSeverity, ...] = ("critical", "major", "minor")
GAP_MATERIAL_SEVERITIES: tuple[GapSeverity, ...] = ("critical", "major")

RepairAction: TypeAlias = Literal[
    "extend_plan",
    "acquire",
    "assess_source",
    "adjudicate",
    "consolidate",
    "synthesize",
]
REPAIR_ACTIONS: tuple[RepairAction, ...] = (
    "extend_plan",
    "acquire",
    "assess_source",
    "adjudicate",
    "consolidate",
    "synthesize",
)

# The action that runs a search. Queries are acquisition only: a rewrite, an
# adjudication, a consolidation, a source assessment, or a plan extension runs
# no search, so a query attached to one is a contract violation rather than a
# harmless extra.
QUERY_BEARING_REPAIR_ACTION: RepairAction = "acquire"

#: The reserved ``target_ids`` entry that scopes a defect to the whole answer.
#:
#: An original-question omission has no planned target to name — that is what
#: makes it an omission — so it points at the question instead of inventing a
#: topic id. It is also the honest scope of a legacy gap that named no plan id
#: at all, because such a gap was only ever listed when closing it would change
#: the answer to the question.
QUESTION_TARGET_ID = "question"


def gap_contract_problem(
    *,
    kind: str,
    severity: str,
    repair_action: str,
    coverage_id: str | None,
    target_ids: Sequence[str],
    statement_ids: Sequence[str],
    claim_cluster_ids: Sequence[str],
    recommended_queries: Sequence[str],
) -> str | None:
    """Why one gap is not actionable, or ``None`` when it is.

    The single rule, shared by both boundaries that carry a gap: the
    provider-facing draft and the state-facing contract. It is stated once
    because the two must agree — a reply the provider schema accepts and the
    contract then rejects would abort the review instead of being repaired,
    and a rule copied into the second boundary is a rule that can drift out of
    the first.

    "Actionable" means routable: a material gap names at least one target,
    statement, cluster, or planned sub-topic it affects; ``acquire`` names the
    target (or sub-topic) whose obligation is unmet, because acquisition is
    per obligation and a statement id says which sentence is thin rather than
    what evidence is owed; and search queries ride on an acquisition gap and
    on nothing else.

    Every scope token is stripped before it counts, so a whitespace-only id is
    absent rather than present. The scalar fields are stripped by the model and
    the list fields are not, and that asymmetry was the same harm through a
    sibling field: ``target_ids=[""]`` satisfied "must name what it affects"
    and was then dropped downstream, leaving a material gap with no scope.
    """
    if kind == "presentation" and recommended_queries:
        return (
            "a presentation gap is a rewrite, not a search; it carries no "
            "queries"
        )
    scope = [coverage_id.strip()] if coverage_id and coverage_id.strip() else []
    targets = [item.strip() for item in target_ids if item.strip()]
    affected = [
        *scope,
        *targets,
        *(item.strip() for item in statement_ids if item.strip()),
        *(item.strip() for item in claim_cluster_ids if item.strip()),
    ]
    if severity in GAP_MATERIAL_SEVERITIES and not affected:
        return (
            "a critical or major gap must name the target, statement, or "
            "claim cluster it affects"
        )
    if repair_action == QUERY_BEARING_REPAIR_ACTION and not (scope or targets):
        return (
            "an acquire gap must name the target, or the planned sub-topic, "
            "whose evidence obligation is missing"
        )
    if (
        repair_action != QUERY_BEARING_REPAIR_ACTION
        and recommended_queries
    ):
        return (
            "search queries belong to an acquisition gap only; this gap "
            f"repairs by {repair_action} and runs no search"
        )
    return None


class CritiqueGap(ContractModel):
    """One targetable report problem returned by the Critic.

    Every field but ``gap_id`` and ``problem`` is additive over the first
    contract, so a snapshot written before this one still reads: such a gap
    resolves to ``coverage``/``major``/``acquire``, which is exactly what the
    old prompt asked the model for ("list a gap only when closing it would
    materially change the answer"). The severity default is what keeps a
    legacy gap material — a default of ``minor`` would silently stop it
    routing anywhere.

    The validators encode what makes a defect *actionable*, because a vague
    gap cannot be routed: a material gap names at least one target, statement,
    or claim cluster it affects; ``acquire`` names the target whose obligation
    is unmet (acquisition is per obligation, and a statement id says which
    sentence is thin, not what evidence is owed); and search queries ride on
    an acquisition gap and on nothing else.
    """

    gap_id: str = ""
    """The bounded, project-stamped identity of this gap within one review."""
    coverage_id: str | None = None
    """The planned sub-topic this gap belongs to, when one is named.

    Kept beside ``target_ids`` rather than replaced by it: the Researcher
    groups refinement work by coverage id, and a gap that names a target
    still belongs to the sub-topic that target was planned under.
    """
    target_ids: list[str] = Field(default_factory=list)
    claim_cluster_ids: list[str] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    kind: GapKind = "coverage"
    severity: GapSeverity = "major"
    repair_action: RepairAction = "acquire"
    problem: str = Field(min_length=1)
    recommended_queries: list[str] = Field(default_factory=list)

    @property
    def material(self) -> bool:
        """True when this gap must be closed before the report is accepted."""
        return self.severity in GAP_MATERIAL_SEVERITIES

    @model_validator(mode="before")
    @classmethod
    def accept_a_prescope_gap(cls, values: object) -> object:
        """Keep a pre-Task-8 gap readable, exactly where it really is one.

        A gap that declares neither a severity nor any scope is the shape this
        contract replaced: nothing in the old schema *could* state either, and
        the old prompt asked for a gap only when closing it would change the
        answer. Scoping it to the whole answer is therefore what it always
        meant, and it is the only scope that invents nothing.

        The rule is deliberately narrow. A payload that declares a severity is
        held to the new contract, and a payload that names any target,
        statement, cluster, or sub-topic is left exactly as it came — so a
        provider gap that forgot to say what it affects still fails loudly
        instead of being waved through with a scope it never claimed.
        """
        if not isinstance(values, dict):
            return values
        if {"severity", "kind", "repair_action"}.intersection(values):
            return values
        if any(
            values.get(field)
            for field in (
                "coverage_id",
                "target_ids",
                "statement_ids",
                "claim_cluster_ids",
            )
        ):
            return values
        return {**values, "target_ids": [QUESTION_TARGET_ID]}

    @model_validator(mode="after")
    def validate_actionable(self) -> "CritiqueGap":
        problem = gap_contract_problem(
            kind=self.kind,
            severity=self.severity,
            repair_action=self.repair_action,
            coverage_id=self.coverage_id,
            target_ids=self.target_ids,
            statement_ids=self.statement_ids,
            claim_cluster_ids=self.claim_cluster_ids,
            recommended_queries=self.recommended_queries,
        )
        if problem is not None:
            raise ValueError(problem)
        return self


# What a critique is worth when nothing could be judged at all. ``reviewed`` is
# every critique a model produced; ``failed`` records that the reply never
# validated, so the score beside it is the floor rather than a judgement. The
# distinction has to be on the record: "the report scored 1" and "the report
# was never scored" route the same way but mean opposite things.
CritiqueReviewStatus: TypeAlias = Literal["reviewed", "failed"]


class Critique(ContractModel):
    score: CriticScore
    gaps: list[CritiqueGap]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    should_continue: bool
    rationale: str = Field(min_length=1)
    review_status: CritiqueReviewStatus = "reviewed"

    @model_validator(mode="after")
    def stamp_gap_ids(self) -> "Critique":
        """Give every gap a bounded id, without disturbing one it has.

        A review's gaps are addressed by id — Task 9 routes by them and a
        reader cites them — and a gap built by hand or read back from an older
        snapshot carries none. The id is positional within this review and
        never content-derived: the same defect twice in two reviews must not
        look like one finding.
        """
        self.gaps = [
            gap
            if gap.gap_id
            else gap.model_copy(update={"gap_id": f"gap-{index + 1:02d}"})
            for index, gap in enumerate(self.gaps)
        ]
        return self

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


# --- Task 10: the terminal semantic report review --------------------------
#
# The seven reader-facing dimensions keep the names the whole-report campaign
# already used, so a historical artifact and a semantic review speak the same
# vocabulary. What changed is what each one *means*: it is a judgement about
# the report's substance, made by a reviewer that reads the report and the
# evidence behind it, rather than a formula over counts.
REVIEW_DIMENSIONS: frozenset[str] = frozenset(
    {
        "completeness",
        "prioritization",
        "evidence_quality",
        "attribution",
        "uncertainty",
        "readability",
        "actionability",
    }
)
"""Exactly the seven dimensions a semantic review scores, and no others."""

SEMANTIC_REVIEW_MEAN: float = 0.80
"""The mean over ``REVIEW_DIMENSIONS`` a review must reach to pass."""

REVIEW_RUBRIC_VERSION = 2
"""Which semantic rubric a review was made under.

Version 1 is the structural formula (``judge_whole_report``); version 2 is the
seven semantic definitions. The version travels on the record so a historical
diagnostic and a semantic judgement can never be compared as if they were the
same measurement.
"""

# How one review ended. Deliberately a *third* vocabulary, distinct from
# ``CritiqueReviewStatus`` (the Critic's ``reviewed``/``failed``) and from
# ``RepairStopReason`` (why the machine stopped): a semantic review that could
# not be completed, a Critic that never produced a review, and a repair loop
# that ran out of leads are three different facts, and collapsing any two of
# them is how "no judgement" starts reading as a verdict.
ReportReviewStatus: TypeAlias = Literal["scored", "incomplete", "provider_failed"]
REPORT_REVIEW_STATUSES: tuple[ReportReviewStatus, ...] = (
    "scored",
    "incomplete",
    "provider_failed",
)

# What one review concluded about one reader statement. ``supported`` and
# ``attributed`` are the two ways a statement may stand on its evidence
# (independently corroborated, or primary-source attribution); ``inference`` is
# a recorded derivation; ``unsupported`` is a statement the evidence does not
# carry; and ``returned_to_fact_checker`` is the disposition for prose the
# reviewer cannot settle from the evidence it was shown — the vocabulary Task
# 7's cell attestation needed and could not reach offline.
StatementReviewDisposition: TypeAlias = Literal[
    "supported",
    "attributed",
    "inference",
    "unsupported",
    "returned_to_fact_checker",
    "not_reviewed",
]
STATEMENT_REVIEW_DISPOSITIONS: tuple[StatementReviewDisposition, ...] = (
    "supported",
    "attributed",
    "inference",
    "unsupported",
    "returned_to_fact_checker",
    "not_reviewed",
)

UNREVIEWED_STATEMENT_DISPOSITION: StatementReviewDisposition = "not_reviewed"
"""The disposition of a statement the review never reached.

The honest default, and never a pass: a scored review may not carry one, so
missing coverage cannot be recorded as a clean result.
"""

UNSETTLED_STATEMENT_DISPOSITIONS: frozenset[str] = frozenset(
    {"unsupported", "returned_to_fact_checker", UNREVIEWED_STATEMENT_DISPOSITION}
)
"""Dispositions that mean "this statement is not established as written".

``not_reviewed`` is included because an unexamined statement is not an
accepted one: both must keep the review from passing, and both must be
representable as a material defect so the routing layer can see them.
"""


class ReportReview(ContractModel):
    """One complete, source-bound judgement of the reader report.

    The terminal counterpart of the Critic's review, and deliberately its own
    contract: the Critic judges whether more research is worth buying from the
    packet it was handed, while this judges whether the *report* answers the
    question on the evidence the run actually holds. Both can be present, and
    they disagree often enough that merging them would lose the disagreement.

    ``status`` is this review's own three-valued outcome. A ``scored`` review
    is one that saw the whole report, covered every reader statement and every
    piece of evidence it was given, and returned exactly the seven dimensions
    over a fingerprint that still matches the packet: the model validators
    below refuse the combination that would let a partial review claim a
    score. ``incomplete`` and ``provider_failed`` carry no score at all —
    ``dimensions`` stays empty — because a missing judgement must not be
    averageable into an acceptance.

    ``per_statement_dispositions`` is where a statement the reviewer could not
    settle is recorded, and it is load-bearing: a scored review whose
    dispositions leave a statement unsupported *must* carry a material defect
    naming it (``derived_defect_statement_ids`` says which defects this project
    derived from a disposition rather than the reviewer returning). That is
    what keeps one narrow judgement — "this sentence is not in the source" —
    from being recorded as an observation while the review still passes.
    """

    status: ReportReviewStatus = "incomplete"
    dimensions: dict[str, UnitScore] = Field(default_factory=dict)
    defects: list[CritiqueGap] = Field(default_factory=list)
    per_statement_dispositions: dict[str, StatementReviewDisposition] = Field(
        default_factory=dict
    )
    reviewed_statement_ids: list[str] = Field(default_factory=list)
    unreviewed_statement_ids: list[str] = Field(default_factory=list)
    reviewed_evidence_ids: list[str] = Field(default_factory=list)
    omitted_evidence_ids: list[str] = Field(default_factory=list)
    reviewed_batch_ids: list[str] = Field(default_factory=list)
    expected_batch_ids: list[str] = Field(default_factory=list)
    reviewed_target_ids: list[str] = Field(default_factory=list)
    derived_defect_statement_ids: list[str] = Field(default_factory=list)
    """Statements whose material defect this project derived from a disposition."""
    input_fingerprint: str = ""
    composition_fingerprint: str = ""
    """The semantic fingerprint of the composition this judgement was made over.

    Read by ``merge_research_state``: a composition replacement invalidates the
    stored review unless this still matches the incoming composition, so a
    judgement can never be carried over to a report whose content, references,
    or targets changed. It is not the same value as ``input_fingerprint`` —
    that one covers the packet the reviewer actually read, including the
    state-level evidence and coverage the composition does not carry.
    """
    rubric_version: int = Field(default=REVIEW_RUBRIC_VERSION, ge=1)
    rationale: str = ""

    @property
    def coverage_complete(self) -> bool:
        """True when every statement and every batch the packet carried was read."""
        if self.unreviewed_statement_ids:
            return False
        return not set(self.expected_batch_ids).difference(self.reviewed_batch_ids)

    @property
    def material_defects(self) -> list[CritiqueGap]:
        """The defects that must be closed before the report may be accepted."""
        return [gap for gap in self.defects if gap.severity in GAP_MATERIAL_SEVERITIES]

    @property
    def unsettled_statement_ids(self) -> list[str]:
        """Statements this review did not establish as written, in id order."""
        return sorted(
            statement_id
            for statement_id, disposition in self.per_statement_dispositions.items()
            if disposition in UNSETTLED_STATEMENT_DISPOSITIONS
        )

    @property
    def mean_score(self) -> float | None:
        """The mean over the seven dimensions, or ``None`` without a full set."""
        if set(self.dimensions) != REVIEW_DIMENSIONS:
            return None
        return sum(self.dimensions.values()) / len(REVIEW_DIMENSIONS)

    @model_validator(mode="after")
    def validate_scored_review(self) -> "ReportReview":
        """A scored review must actually be one.

        Stated on the type rather than only in the acceptance helper, because
        the record is what persists: a review that says ``scored`` while one
        statement went unread, a dimension is missing, or no fingerprint is
        named would be read by every later consumer as a complete judgement.
        ``semantic_review_passes`` still re-checks all of it — the type keeps
        the record honest, and the helper keeps the *decision* from trusting
        the record.
        """
        if self.status != "scored":
            if self.dimensions:
                raise ValueError(
                    "a review that is not scored carries no dimension scores"
                )
            return self
        missing = REVIEW_DIMENSIONS.difference(self.dimensions)
        if missing or set(self.dimensions).difference(REVIEW_DIMENSIONS):
            raise ValueError(
                "a scored review scores exactly the seven review dimensions"
            )
        if not self.input_fingerprint.strip():
            raise ValueError(
                "a scored review must name the packet fingerprint it judged"
            )
        if self.unreviewed_statement_ids:
            raise ValueError(
                "a scored review must cover every reader statement it was given"
            )
        undispositioned = sorted(
            statement_id
            for statement_id in self.reviewed_statement_ids
            if statement_id not in self.per_statement_dispositions
        )
        if undispositioned:
            # Reading a statement is not judging it. Without this the reply's
            # own account of what it read would be the only record, and a
            # review could claim a complete per-statement reading while
            # recording no judgement of any statement it read.
            raise ValueError(
                "a scored review must record a disposition for every statement "
                "it reviewed: " + ", ".join(undispositioned)
            )
        unscoped = sorted(
            statement_id
            for statement_id in self.unsettled_statement_ids
            if not any(
                statement_id in gap.statement_ids for gap in self.material_defects
            )
        )
        if unscoped:
            raise ValueError(
                "an unsettled statement must be named by a material defect: "
                + ", ".join(unscoped)
            )
        return self


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


# How a reader statement stands in the report, which is a different question
# from what the evidence behind it was adjudicated as. ``Claim.evidence_status``
# answers "was this independently corroborated, only attributed, or contested";
# this answers "how is it allowed to read". ``settled`` is reserved for a
# statement whose every supporting cluster carries the strict independent pair,
# ``attributed`` is primary-source attribution that is not corroboration,
# ``inference`` is a locally recorded derivation that must name its premises,
# ``contested`` states a disagreement without settling it, and ``context`` is
# framing this pass composed rather than a research finding.
#
# Deliberately a separate enumeration from ``Claim.evidence_status``: reusing
# one field for both would let a reader-statement mode be read as an
# adjudication badge (or the reverse) with nothing to catch the mistake.
StatementMode: TypeAlias = Literal[
    "settled",
    "attributed",
    "inference",
    "contested",
    "context",
]

#: The modes that assert something about the world. A statement in one of
#: these modes must resolve to selected evidence or to a checked claim; only
#: ``context`` may be published without an evidence link.
SUBSTANTIVE_STATEMENT_MODES: tuple[StatementMode, ...] = (
    "settled",
    "attributed",
    "inference",
    "contested",
)

#: The modes that can discharge a planned obligation. A different question
#: from ``SUBSTANTIVE_STATEMENT_MODES``: that one asks "must this carry an
#: evidence link" (yes for ``contested`` — a recorded disagreement still has
#: to cite what it disagrees about), this one asks "can this count as the
#: answer to a target" (no for ``contested`` — a disagreement recorded
#: without settling it is not an answer, and letting it satisfy a target's
#: obligation would let a target report answered while the report itself
#: still states the fact is disputed).
ANSWERING_STATEMENT_MODES: tuple[StatementMode, ...] = (
    "settled",
    "attributed",
    "inference",
)

# The qualitative reading of an evidence badge, shared by every surface that
# reports one — the model-facing packet and the reader report. A reader should
# learn whether an assertion was independently corroborated, only attributed
# to its own publisher, disputed, or never classified; ``0.90`` is a model
# judgement, not a calibrated probability, and printing it as one invites a
# reader to weigh it as a frequency it never was. The number stays in the
# evidence ledger's claim registry, where the caveat can sit beside it.
EVIDENCE_BADGE_LABELS: dict[str, str] = {
    "verified_pair": "independently corroborated",
    "source_supported": (
        "primary-source attribution; independent corroboration not established"
    ),
    "contested": "contested; both sides recorded",
    "": "no corroboration classification recorded",
}


class ReportStatement(ContractModel):
    """One reader statement, with the evidence mapping that makes it auditable.

    Every substantive sentence the reader report prints is one of these: the
    text, the mode it is allowed to read in, the claim clusters it rests on,
    the exact selected evidence ids behind it, the targets and required
    dimensions it answers, and — for a derivation — the ``basis`` that states
    its premises. A statement in a substantive mode with no evidence link is
    invalid, which is what makes "factual prose outside this mapping" a
    validation failure rather than a style problem.

    ``basis`` is a nullable project-readable explanation: the recorded
    derivation for an ``inference`` statement, and the classification reason
    for a ``context`` statement ("not acquired", "uncertain/conflicting",
    "outside scope"). It is required exactly where an inference would
    otherwise be an unstated leap.
    """

    statement_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    mode: StatementMode = "settled"
    claim_cluster_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    answered_dimensions: list[str] = Field(default_factory=list)
    dimension_support: dict[str, list[str]] = Field(default_factory=dict)
    """Each answered dimension mapped to the cluster ids that carry it.

    Recorded at derivation time by ``derive_statement``, per cluster, so a
    target check can tell which claim actually carries a required dimension
    instead of testing the target's support policy against every claim the
    statement resolves to pooled together. Empty means "built before this
    attribution existed, or hand-built by a fixture or an older snapshot" —
    deliberately not "no cluster carries anything": the same polarity as
    ``Claim.insufficient_reason``, where a missing record reads as "not yet
    known" rather than as a negative fact.
    """
    basis: str | None = None

    @property
    def substantive(self) -> bool:
        """True when this statement asserts something about the world."""
        return self.mode in SUBSTANTIVE_STATEMENT_MODES

    @model_validator(mode="after")
    def validate_reader_mode(self) -> ReportStatement:
        """An inference must name the derivation it rests on."""
        if self.mode == "inference" and not (self.basis or "").strip():
            raise ValueError(
                "an inference statement requires a recorded basis: a derived "
                "statement shows its premises"
            )
        return self


class ReportPoint(ContractModel):
    """One settled statement, with the claims and sources it rests on.

    ``claim_ids`` and ``source_urls`` are already validated against the
    checked-claim registry by the time a point reaches a renderer; rendering
    never validates. ``statement`` is the Task 7 record that makes the point
    auditable — the reader mode, the selected evidence ids, the targets and
    dimensions it answers, and any recorded derivation. It is filled for every
    point by ``ReportComposition`` when a caller (a fixture, or a snapshot
    written before the field existed) supplies none, so a rendered point
    always has one.
    """

    text: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    statement: ReportStatement | None = None

    @property
    def statement_id(self) -> str:
        return self.statement.statement_id if self.statement else ""

    @property
    def mode(self) -> StatementMode:
        """The reader mode of this point, or the pre-statement default."""
        return self.statement.mode if self.statement else "settled"

    @property
    def claim_cluster_ids(self) -> list[str]:
        return list(self.statement.claim_cluster_ids) if self.statement else []

    @property
    def evidence_ids(self) -> list[str]:
        return list(self.statement.evidence_ids) if self.statement else []

    @property
    def target_ids(self) -> list[str]:
        return list(self.statement.target_ids) if self.statement else []

    @property
    def answered_dimensions(self) -> list[str]:
        return (
            list(self.statement.answered_dimensions) if self.statement else []
        )

    @property
    def basis(self) -> str | None:
        return self.statement.basis if self.statement else None


class ReportConstraint(ReportPoint):
    """One ranked constraint, plus the two decision columns it prints.

    A constraint row is a claim-linked point like any other; the deployment
    mechanism and the geography are part of the same claim-backed row, and a
    row whose evidence does not state them says ``not stated``.

    Both cells carry their own statement record. A cell is a factual
    assertion like any other: when the evidence behind the row does not
    support what the cell says, the cell is repaired to ``not stated`` and the
    repair is recorded, rather than printed as provider-attested prose with no
    provenance.
    """

    deployment_mechanism: str = ""
    geography: str = ""
    mechanism_statement: ReportStatement | None = None
    geography_statement: ReportStatement | None = None

    @property
    def mechanism_statement_id(self) -> str:
        return (
            self.mechanism_statement.statement_id
            if self.mechanism_statement
            else ""
        )

    @property
    def geography_statement_id(self) -> str:
        return (
            self.geography_statement.statement_id
            if self.geography_statement
            else ""
        )


class ReportAnswerRow(ContractModel):
    """One row of the answer-kind table, as statement-backed cells.

    The cells are in column order: the label cells (the subject and the
    dimension the row is judged on) and, last, the evidenced finding. The
    column contract is positional rather than mode-based, because an attested
    label is a substantive statement too — it names a period or a place the
    evidence carries — and reading "the row's finding" by mode would hand a
    caller the subject cell instead.
    """

    cells: list[ReportStatement] = Field(min_length=1)

    @property
    def statement(self) -> ReportStatement | None:
        """The row's evidenced finding: the last cell, by column contract."""
        return self.cells[-1] if self.cells else None

    @property
    def labels(self) -> list[ReportStatement]:
        """The row's label cells, in column order: all but the finding."""
        return list(self.cells[:-1])


# The atom dimensions a required dimension can be evidenced by. A statement
# answers a target's required dimension only when the recorded proposition
# behind it actually fills one of these atom fields: claiming a dimension the
# evidence does not carry is the coverage defect this table exists to prevent.
_DIMENSION_SIGNALS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("period", "time", "date", "year", "when", "horizon", "recency"),
        ("observation_period", "forecast_status"),
    ),
    (
        ("geography", "region", "place", "location", "country", "jurisdiction"),
        ("geography",),
    ),
    (
        (
            "scale",
            "magnitude",
            "quantity",
            "size",
            "capacity",
            "amount",
            "value",
            "rate",
            "level",
        ),
        ("value", "unit"),
    ),
    (("population", "subject", "entity", "who"), ("subject", "population")),
    (
        ("mechanism", "how", "cause", "driver", "method", "instrument"),
        ("predicate", "quantity_noun"),
    ),
    (("attribution", "source", "issuer", "publisher"), ("attribution",)),
    (
        ("share", "proportion", "percent", "denominator", "comparison"),
        ("denominator",),
    ),
)


def answered_atom_dimensions(
    propositions: Sequence[AtomicProposition],
) -> list[str]:
    """The atom fields the recorded propositions fill, in signal order.

    Per field, not per field group: a proposition that states an observation
    period has not stated a forecast status, and a caller reading this list as
    a record of what the evidence carries would be wrong to think otherwise.
    """
    filled: list[str] = []
    for _, fields in _DIMENSION_SIGNALS:
        for name in fields:
            if name in filled:
                continue
            if any(getattr(proposition, name) for proposition in propositions):
                filled.append(name)
    return filled


def answered_required_dimensions(
    dimensions: Sequence[str],
    propositions: Sequence[AtomicProposition],
) -> list[str]:
    """The required dimensions this recorded evidence actually carries.

    A dimension whose wording matches no recorded atom field is *not*
    answered: an unmatched obligation is an unanswered one, and saying
    otherwise would let a statement claim coverage it cannot show.
    """
    evidenced = answered_atom_dimensions(propositions)
    answered: list[str] = []
    for dimension in dimensions:
        tokens = {
            token.strip(".,:;()").casefold()
            for token in dimension.replace("-", " ").replace("_", " ").split()
        }
        if not tokens:
            continue
        for signals, fields in _DIMENSION_SIGNALS:
            if tokens & set(signals) and any(
                name in evidenced for name in fields
            ):
                if dimension not in answered:
                    answered.append(dimension)
                break
    return answered


class ReportSection(ContractModel):
    """One validated theme of the findings, as claim-linked points."""

    title: str = Field(min_length=1)
    points: list[ReportPoint] = Field(default_factory=list)


def statement_mode_for_claims(claims: Sequence[Claim]) -> StatementMode:
    """The reader mode the evidence behind a statement actually supports.

    ``settled`` is never granted by a verdict alone: every claim behind the
    statement must carry the strict independent-pair badge. Anything less is
    attribution, a disagreement is contested, and a statement resting on no
    checked claim at all is context — this pass's own framing.
    """
    if not claims:
        return "context"
    if any(
        claim.evidence_status == "contested" or claim.verdict == "contradicted"
        for claim in claims
    ):
        return "contested"
    if all(
        claim.verdict == "verified" and claim.evidence_status == "verified_pair"
        for claim in claims
    ):
        return "settled"
    return "attributed"


def required_dimensions_for_targets(
    composition: ReportComposition,
    target_ids: Sequence[str],
) -> list[str]:
    """The required dimensions the named targets carry, in plan order."""
    wanted = set(target_ids)
    dimensions: list[str] = []
    for topic in composition.sub_topics:
        for target in topic.evidence_targets:
            if target.target_id not in wanted:
                continue
            for dimension in target.required_dimensions:
                if dimension not in dimensions:
                    dimensions.append(dimension)
    return dimensions


def dimensions_by_target(
    targets: Sequence[EvidenceTarget],
) -> dict[str, list[str]]:
    """Each target's required dimensions, keyed by target id.

    The one input shape the shared derivation takes from either caller: the
    Synthesizer builds it from its task's targets, the composition from its
    own plan, and neither has to know how the other stores them.
    """
    return {
        target.target_id: list(target.required_dimensions) for target in targets
    }


def clusters_for_claims(
    claims: Sequence[Claim],
    clusters: Mapping[str, ClaimCluster],
) -> list[str]:
    """Every cluster id the named claims resolve to, in registry order.

    Resolved in both directions on purpose: a claim records the cluster it
    joined, and a cluster records the claims it absorbed. A refinement that
    persisted only one side still resolves, and a merge that absorbed an alias
    still resolves through it.
    """
    member_ids = {claim.claim_id for claim in claims}
    resolved: list[str] = []
    for claim in claims:
        for candidate in (claim.cluster_id, *claim.cluster_aliases):
            if (
                candidate
                and candidate in clusters
                and candidate not in resolved
            ):
                resolved.append(candidate)
    for cluster_id, cluster in clusters.items():
        if cluster_id in resolved:
            continue
        if member_ids & set(cluster.member_claim_ids):
            resolved.append(cluster_id)
    return resolved


def derive_statement(
    *,
    statement_id: str,
    text: str,
    claims: Sequence[Claim],
    clusters: Mapping[str, ClaimCluster],
    evidence: Mapping[str, EvidenceUnit],
    dimensions_by_target: Mapping[str, Sequence[str]],
    basis: str = "",
    mode: StatementMode | None = None,
) -> ReportStatement:
    """The one statement derivation both the draft and legacy paths use.

    Resolves the clusters the claims belong to in both directions, unions the
    cluster and claim evidence ids in that order — keeping only ids the
    evidence registry can answer — unions the cluster and claim target ids in
    that order, and records the dimensions the recorded atoms actually carry.

    Two near-verbatim copies of this lived in ``agents.synthesizer`` and here,
    and they had already diverged: one forced ``inference`` when a basis was
    present and the other never set a basis at all, so a later fix to either
    half would have left the other wrong at exactly the boundary between the
    validated path and the fixture path.
    """
    cluster_ids = clusters_for_claims(claims, clusters)
    evidence_ids: list[str] = []
    for cluster_id in cluster_ids:
        for evidence_id in clusters[cluster_id].evidence_ids:
            if evidence_id in evidence and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    for claim in claims:
        for evidence_id in claim.evidence_selection:
            if evidence_id in evidence and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    target_ids: list[str] = []
    for claim in claims:
        for target_id in claim.target_ids:
            if target_id not in target_ids:
                target_ids.append(target_id)
    for cluster_id in cluster_ids:
        for target_id in clusters[cluster_id].target_ids:
            if target_id not in target_ids:
                target_ids.append(target_id)
    required: list[str] = []
    for target_id in target_ids:
        for dimension in dimensions_by_target.get(target_id, ()):
            if dimension not in required:
                required.append(dimension)
    # Attributed per cluster, not pooled: each cluster's own recorded
    # proposition is checked against ``required`` on its own, and the
    # dimension is credited only to the clusters that actually carry it. By
    # construction this unions to the exact same set ``answered_dimensions``
    # would be if computed by pooling every proposition up front — see
    # ``test_recorded_dimension_support_unions_to_the_answered_dimensions``
    # — but it also records *which* cluster is answerable for, which is what
    # lets a target check scope its support-policy test to the right claim
    # instead of testing it against every claim the statement resolves to.
    dimension_support: dict[str, list[str]] = {}
    for cluster_id in cluster_ids:
        proposition = clusters[cluster_id].proposition
        for dimension in answered_required_dimensions(required, [proposition]):
            dimension_support.setdefault(dimension, []).append(cluster_id)
    answered_dimensions = [
        dimension for dimension in required if dimension in dimension_support
    ]
    base_mode: StatementMode = statement_mode_for_claims(claims)
    recorded_mode: StatementMode = mode or (
        "inference" if basis.strip() and base_mode != "contested" else base_mode
    )
    return ReportStatement(
        statement_id=statement_id,
        text=text,
        mode=recorded_mode,
        claim_cluster_ids=cluster_ids,
        evidence_ids=evidence_ids,
        target_ids=target_ids,
        answered_dimensions=answered_dimensions,
        dimension_support=dimension_support,
        basis=basis.strip() or None,
    )


def statement_for_point(
    composition: ReportComposition,
    point: ReportPoint,
    *,
    statement_id: str,
) -> ReportStatement:
    """The record behind one point, derived from the evidence it names.

    The composition's half of the shared derivation: it resolves the claims
    the point names and the plan's target dimensions, and hands both to
    ``derive_statement``. Used by the composition itself, which fills a record
    in for a point that was built by hand or written before this contract
    existed. Nothing is invented, so an unresolvable point comes out with
    empty ids and is caught by the mapping validator.
    """
    claims_by_id = {claim.claim_id: claim for claim in composition.claims}
    claims = [
        claims_by_id[claim_id]
        for claim_id in point.claim_ids
        if claim_id in claims_by_id
    ]
    return derive_statement(
        statement_id=statement_id,
        text=point.text,
        claims=claims,
        clusters=composition.claim_clusters,
        evidence=composition.evidence_units,
        dimensions_by_target=dimensions_by_target(
            [
                target
                for topic in composition.sub_topics
                for target in topic.evidence_targets
            ]
        ),
    )


def _fill_statement_map(composition: ReportComposition) -> None:
    """Give every rendered statement a record, without disturbing one.

    A statement the producer already built is kept exactly as it was: this is
    the compatibility path for a fixture or an older snapshot, and rewriting
    a record the Synthesizer validated would silently discard the mode,
    derivation, and dispositions it recorded.
    """
    counters = {"S": 0, "C": 0, "F": 0, "A": 0, "U": 0}

    def next_id(prefix: str) -> str:
        counters[prefix] += 1
        return f"{prefix}{counters[prefix]:03d}"

    def fill_point(point: ReportPoint, prefix: str) -> ReportPoint:
        if point.statement is not None:
            return point
        return point.model_copy(
            update={
                "statement": statement_for_point(
                    composition, point, statement_id=next_id(prefix)
                )
            }
        )

    composition.summary = [
        fill_point(point, "S") for point in composition.summary
    ]
    filled_constraints: list[ReportConstraint] = []
    for row in composition.constraints:
        row = fill_point(row, "C")
        cells: dict[str, ReportStatement] = {}
        for field_name, cell_text in (
            ("mechanism_statement", row.deployment_mechanism),
            ("geography_statement", row.geography),
        ):
            existing = getattr(row, field_name)
            if existing is not None:
                continue
            cells[field_name] = ReportStatement(
                statement_id=next_id("C"),
                text=cell_text.strip() or "not stated",
                mode="attributed" if cell_text.strip() else "context",
                claim_cluster_ids=list(row.claim_cluster_ids),
                evidence_ids=list(row.evidence_ids),
                target_ids=list(row.target_ids),
                answered_dimensions=list(row.answered_dimensions),
                basis=(
                    "cell carried by the row's evidence"
                    if cell_text.strip()
                    else "the row's evidence does not state this cell"
                ),
            )
        filled_constraints.append(
            row.model_copy(update=cells) if cells else row
        )
    composition.constraints = filled_constraints
    composition.sections = [
        section.model_copy(
            update={
                "points": [
                    fill_point(point, "F") for point in section.points
                ]
            }
        )
        for section in composition.sections
    ]
    if not composition.uncertainty_statements:
        composition.uncertainty_statements = [
            ReportStatement(
                statement_id=next_id("U"),
                text=note,
                mode="context",
                basis="uncertainty note carried by this pass",
            )
            for note in composition.uncertainty_notes
            if note.strip()
        ]


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

    ``answer_kind``, ``date_basis`` and ``requested_word_limit`` are the
    frozen contract's values, copied here so a renderer never has to
    re-derive them; ``generated_on`` is the run clock's own date, which is
    not the contract's answer date. ``claim_clusters`` and ``evidence_units``
    are the registries that turn a statement's evidence ids into citations.
    All of them are optional so a fixture or a snapshot written before this
    contract still composes, in the shape it always had.
    """

    question: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=0, ge=0)
    as_of: str = ""
    """The newest recorded evidence timestamp; see ``report_as_of``.

    A read's ``retrieved_at`` or a finding's ``extracted_at`` — never a graph
    event timestamp and never a clock read. The run clock's own date is
    ``generated_on``.
    """
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
    answer_kind: AnswerKind | None = None
    """The frozen answer form, or ``None`` for a legacy composition."""
    answer_rows: list[ReportAnswerRow] = Field(default_factory=list)
    """The answer-kind table's rows, for every form but ``constraints``."""
    uncertainty_statements: list[ReportStatement] = Field(
        default_factory=list
    )
    """Statement-backed uncertainty prose, grouped by its recorded basis."""
    claim_clusters: dict[str, ClaimCluster] = Field(default_factory=dict)
    evidence_units: dict[str, EvidenceUnit] = Field(default_factory=dict)
    statement_dispositions: list[str] = Field(default_factory=list)
    """Enumerated dispositions for statements this pass refused or repaired."""
    returned_to_fact_checker: list[str] = Field(default_factory=list)
    """New factual assertions this pass detected; Task 9 owns the routing."""
    generated_on: str = ""
    """The run clock's date as ISO ``YYYY-MM-DD``.

    Not the date of the evidence and not the contract's answer date: a
    question that names a date is answered as of that date, but the document
    was still printed on the run's own day.
    """
    date_basis: str = ""
    """Which date the question is actually about, and that asked-for date.

    Carries the contract's ``evidence_period_requirement`` and its frozen
    ``as_of_date``, so a reader sees the date the answer is written as of
    without it being confused with the run's own ``generated_on``.
    """
    requested_word_limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def canonicalize_evidence(self) -> ReportComposition:
        from deep_research.agents.identity import (  # noqa: PLC0415
            merge_claim_snapshot,
            merge_source_snapshot,
        )

        self.sources = merge_source_snapshot([], self.sources)
        self.claims = merge_claim_snapshot([], self.claims)
        _fill_statement_map(self)
        return self

    @property
    def statements(self) -> list[ReportStatement]:
        """Every statement this composition renders, in render order."""
        rows: list[ReportStatement] = []
        for point in [*self.summary, *self.constraints]:
            if point.statement is not None:
                rows.append(point.statement)
        for row in self.constraints:
            for cell in (row.mechanism_statement, row.geography_statement):
                if cell is not None:
                    rows.append(cell)
        for row in self.answer_rows:
            rows.extend(row.cells)
        for section in self.sections:
            for point in section.points:
                if point.statement is not None:
                    rows.append(point.statement)
        rows.extend(self.uncertainty_statements)
        return rows

    @property
    def distinct_statement_count(self) -> int:
        """How many distinct facts the reader statements rest on.

        A brief restatement of one cluster in the summary and a detailed
        discussion of it in the findings are two statements of *one* fact, so
        the fact is counted once. This is the count a length or quality
        metric reads; the rendered bullets are not.
        """
        keys: set[tuple[str, ...]] = set()
        for statement in self.statements:
            if not statement.substantive:
                continue
            key = tuple(sorted(statement.claim_cluster_ids))
            keys.add(key or ("text", " ".join(statement.text.casefold().split())))
        return len(keys)


# --- Task 9: typed repair targets, required-target completion, and progress -
#
# Task 8 gives the Critic a typed defect vocabulary; this is what the graph
# *does* with one. A ``RefinementTarget`` is one repair job: the typed action
# naming the node that performs it, the scope the Critic (or the plan itself)
# declared, and the queries that ride on an acquisition. It is a job rather
# than a log of defects — the critique keeps those — so two defects that route
# identically and affect the same scope are one job with one node to run.

RepairStopReason: TypeAlias = Literal[
    "no_progress",
    "pending_capacity",
    "evidence_unavailable",
    "provider_failure",
    "max_iterations",
]
"""Why a repair loop stopped, and never a verdict on the report.

Kept apart from ``CritiqueReviewStatus`` on purpose: a graph stop says what the
*machine* did, a failed review says the report was never judged. Conflating
them would let "we stopped acquiring" read as "quality failed", and the two
route to different statuses.
"""
REPAIR_STOP_REASONS: tuple[RepairStopReason, ...] = (
    "no_progress",
    "pending_capacity",
    "evidence_unavailable",
    "provider_failure",
    "max_iterations",
)

RefinementOrigin: TypeAlias = Literal[
    "critic_gap", "review_defect", "unanswered_target", "returned_assertion"
]
"""Where one repair job came from.

``critic_gap`` is a defect the Critic named, routed exactly as it typed it.
``review_defect`` is a defect the terminal semantic review named: the same
contract and the same routing, from a reviewer whose input is the report and
its evidence rather than the packet the Critic weighed, so the two stay apart
on the record even when they route identically. ``unanswered_target`` is a
mechanically unmet planned obligation: a required target whose reader
statement does not satisfy it yet. The second origin is what makes "the Critic
said nothing" unable to suppress a required target. ``returned_assertion`` is a
factual assertion the Synthesizer detected and sent back (Task 7's
``ReportComposition.returned_to_fact_checker``); Task 9 owns routing it to
adjudication rather than letting it re-enter the report.
"""


class RefinementTarget(ContractModel):
    """One repair job: a typed action, its scope, and where it came from."""

    gap_id: str = ""
    """The Critic gap this job repairs, or ``""`` for a mechanical defect."""
    coverage_id: str | None = None
    """The planned sub-topic whose work this is, when one is named.

    Read from the gap's own ``coverage_id`` and never re-derived: a gap whose
    scope ids did not resolve was refused at the Critic boundary, so nothing
    here has to guess which topic a defect belongs to.
    """
    target_ids: list[str] = Field(default_factory=list)
    claim_cluster_ids: list[str] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    action: RepairAction
    requested_dimension: str | None = None
    queries: list[str] = Field(default_factory=list)
    origin: RefinementOrigin = "critic_gap"
    severity: GapSeverity = "major"
    problem: str = Field(min_length=1)
    """The representative defect, in the Critic's own words or project text.

    One per job: when two gaps collapse into one route, the more severe one's
    wording is kept. The critique itself remains the record of every gap.
    """

    @property
    def identity(self) -> tuple[object, ...]:
        """What makes two defects one repair job: the action and the scope.

        The action is part of the identity because it *is* the routing
        decision: two gaps that differ only in ``repair_action`` are two jobs
        for two different nodes, and collapsing them would silently adopt one
        node's repair for the other's defect. Severity and problem text are
        deliberately absent — they choose which wording survives, never
        whether a job exists.

        ``extend_plan`` is the one exception, and it is the omission's own
        doing: an original-question omission has no plan id, no cluster and no
        statement, so two of them share every scope field and their texts are
        the only thing that tells them apart. Folding them kept the most
        severe problem and discarded the rest, and the single extension the
        pass bought then closed one omission while the others disappeared from
        the very pass purchased to fix them.
        """
        return (
            self.action,
            self.coverage_id,
            tuple(sorted(self.target_ids)),
            tuple(sorted(self.claim_cluster_ids)),
            tuple(sorted(self.statement_ids)),
            self.requested_dimension,
            self.problem if self.action == "extend_plan" else "",
        )

    @property
    def material(self) -> bool:
        """True when this job must be done before the report can be accepted."""
        return self.severity in GAP_MATERIAL_SEVERITIES

    @property
    def publication_changing(self) -> bool:
        """True when doing this job can change what the report asserts.

        ``synthesize`` rewrites prose over evidence already held, and
        ``extend_plan`` only adds a plan obligation nothing has acquired for
        yet; neither moves a fact. Every other action acquires or re-judges
        evidence, so the report's factual content can move and the review that
        covered the old content no longer covers it.
        """
        return self.action not in ("synthesize", "extend_plan")


class ResearchProgress(ContractModel):
    """One snapshot of what a completed pass substantively changed.

    Deliberately about *state*, not effort: which required targets are now
    answered, which assessed support is new, which gaps closed, and which
    composition was produced. Counts of searches, tool calls, or events are
    absent because they measure spending rather than progress, and
    ``pending_work_ids`` is carried — not scored — so a run that is still
    holding deferred evidence can say so instead of being called stalled.

    ``error_count`` is the one bookkeeping field: how many errors the run had
    recorded when this snapshot was taken. It is not progress either — it is
    the mark that lets the stop decision read only the errors of the pass it is
    judging, so an outage the run already survived cannot be re-read as the
    reason the *next* loop stopped.
    """

    completed_target_ids: list[str] = Field(default_factory=list)
    assessed_support_fingerprints: list[str] = Field(default_factory=list)
    resolved_gap_ids: list[str] = Field(default_factory=list)
    pending_work_ids: list[str] = Field(default_factory=list)
    unresolved_major_gap_ids: list[str] = Field(default_factory=list)
    composition_fingerprint: str = ""
    error_count: int = Field(default=0, ge=0)


def _gained(after: Sequence[str], before: Sequence[str]) -> bool:
    """True when ``after`` holds an entry ``before`` did not."""
    return bool(set(after).difference(before))


def progress_improved(
    before: ResearchProgress,
    after: ResearchProgress,
) -> bool:
    """True when a completed repair changed something substantive.

    Substantive means the answer moved: a required target became completed, a
    gap resolved, an assessed support fingerprint appeared, a material defect
    closed, or the composition itself changed — a repaired duplicate paragraph
    is presentation progress. It deliberately ignores ``pending_work_ids``: an
    extra search, an extra candidate page, or one more queued read is *work*,
    and scoring work as progress is how a run spends its whole budget
    rediscovering that nothing new exists. Neither a critic score nor critic
    wording is read here: neither is a fact about the report.
    """
    if _gained(after.completed_target_ids, before.completed_target_ids):
        return True
    if _gained(after.resolved_gap_ids, before.resolved_gap_ids):
        return True
    if _gained(before.unresolved_major_gap_ids, after.unresolved_major_gap_ids):
        return True
    if _gained(
        after.assessed_support_fingerprints, before.assessed_support_fingerprints
    ):
        return True
    return after.composition_fingerprint != before.composition_fingerprint


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
    quality_path: str | None = None
    """The file the quality record was published under, or ``None``.

    The third artifact of one publication, and written the same way the other
    two are: only the terminal finalizer sets it, only from a write that
    succeeded, and only when the whole set was written. An incomplete set
    publishes no paths at all, so ``None`` here means "this session has no
    advertised quality record", never "read an earlier pass's file".
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
    report_review: ReportReview | None = None
    """The terminal semantic judgement of ``report``, or ``None``.

    Replaced only by a review of the same semantic input fingerprint: a
    composition whose content, references, or targets changed is a different
    report, and a judgement of the old one cannot vouch for it. The quality
    snapshot's ``semantic_review_*`` fields are filled from this record, so a
    consumer that reads only the snapshot still sees which judgement stood —
    and sees that there was none.
    """
    refinement_targets: list[RefinementTarget] = Field(default_factory=list)
    """The repair jobs the graph routed for the pass it is about to run.

    Replaced per pass rather than accumulated: this is the worklist of the
    current repair loop, and a job that is still owed reappears in the next
    pass by construction — it is either still an unresolved defect or still an
    unanswered required target. Persisting it is what lets a resumed run name
    exactly what it was about to repair.
    """
    progress_history: list[ResearchProgress] = Field(default_factory=list)
    """One snapshot per completed pass, oldest first, bounded.

    Bounded by the macro-iteration ceiling plus the initial checkpoint: a
    snapshot per pass is all the stop decision reads, and an unbounded history
    would grow the checkpoint without ever being consulted.
    """
    repair_stop_reason: RepairStopReason | None = None
    """Why the last repair loop stopped, or ``None`` while it has not."""
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
    report_review: ReportReview | None
    refinement_targets: list[RefinementTarget]
    progress_history: list[ResearchProgress]
    repair_stop_reason: RepairStopReason | None
    max_iterations: int
    memory_context: MemorySnapshot
    events: list[ResearchEvent]
    errors: list[ResearchError]


# --- Task 9: is a planned obligation actually answered? ---------------------
#
# Section 2.3 states the rule: a target is answered only when its reader
# statement satisfies its required dimensions and its support policy. Every
# earlier stage measured something weaker — a finding exists, a claim exists,
# a topic was researched — and "one raw metadata finding means this topic is
# done" is precisely the shortcut that skipped the economics topic in the
# reviewed baseline. These two functions are that rule, stated once, so the
# Researcher's eligibility test and the graph's progress decision cannot
# disagree about what "answered" means.


def _canonical_dimension(value: str) -> str:
    return " ".join(value.replace("-", " ").replace("_", " ").split()).casefold()


def statement_claims_by_cluster(
    composition: ReportComposition,
    statement: ReportStatement,
) -> dict[str, list[Claim]]:
    """The checked claims one statement rests on, keyed by cluster id.

    Resolved through both directions of the cluster link: a statement names
    clusters, a claim names the cluster it joined, and a refinement that
    persisted only one side still resolves. ``statement_claims`` is the
    deduplicated, ordered flatten of this map — the one resolver both read.

    Only the first-registered claim per cluster id is consulted here (the
    ``by_cluster.setdefault`` below), matching the resolution this function
    replaces: broadening that to every member claim would loosen the gate a
    support-policy check reads this map for, which is out of scope for a fix
    that is tightening it.
    """
    by_id: dict[str, Claim] = {}
    by_cluster: dict[str, Claim] = {}
    for claim in composition.claims:
        by_id[claim.claim_id] = claim
        if claim.cluster_id:
            by_cluster.setdefault(claim.cluster_id, claim)
        for alias in claim.cluster_aliases:
            by_cluster.setdefault(alias, claim)

    resolved: dict[str, list[Claim]] = {}
    for cluster_id in statement.claim_cluster_ids:
        claim = by_id.get(cluster_id) or by_cluster.get(cluster_id)
        if claim is not None:
            resolved[cluster_id] = [claim]
    return resolved


def statement_claims(
    composition: ReportComposition,
    statement: ReportStatement,
) -> list[Claim]:
    """The checked claims one statement rests on, in the order it names them."""
    resolved: list[Claim] = []
    for claims in statement_claims_by_cluster(composition, statement).values():
        for claim in claims:
            if claim not in resolved:
                resolved.append(claim)
    return resolved


def statement_satisfies_support_policy(
    statement: ReportStatement,
    claims: Sequence[Claim],
    *,
    support_policy: str,
) -> bool:
    """Whether a statement's evidence carries the policy its target declared.

    ``independent_pair`` is the strict badge and nothing less: a claim with
    primary-source attribution is not a corroborated pair, and treating it as
    one is the "one publisher is a pair" defect Section 2.1 names. The weaker
    policies accept that attribution, and ``derivation`` additionally accepts
    a recorded inference — a statement that shows its premises has done what a
    derivation owes, whether or not a lone publisher is behind them.

    The inference escape still requires attributed premises: a recorded basis
    is model prose, so accepting it over unverified or contested claims would
    let the writer satisfy an obligation with nothing behind it. Attribution
    is the floor either way, and the escape only ever adds the derivation
    case on top of it.
    """
    if support_policy == "independent_pair":
        return any(
            claim.verdict == "verified"
            and claim.evidence_status == "verified_pair"
            for claim in claims
        )
    attributed = any(
        claim.evidence_status in ("verified_pair", "source_supported")
        for claim in claims
    )
    if support_policy == "derivation":
        return attributed and (
            statement.mode != "inference"
            or bool((statement.basis or "").strip())
        )
    if support_policy == "primary_attribution":
        return attributed
    return False


def answering_statement_for(
    state: ResearchState, target: EvidenceTarget
) -> ReportStatement | None:
    """The one reader statement that satisfies this target, or ``None``.

    Deliberately strict, and deliberately not "some evidence exists": the
    statement has to name the target, assert something (``context`` and
    ``contested`` are not answers), fill every required dimension the target
    declared, and rest on evidence that carries the target's support policy.
    A state with no composition answers nothing — a pass that composed no
    report has shown no reader statement for any obligation.

    This is the single definition of whether an obligation is answered; every
    reader names it, so the Critic's view of what is still open cannot drift
    from the gate that decides coverage.
    """
    composition = state.composition
    if composition is None:
        return None
    required = {
        _canonical_dimension(dimension)
        for dimension in target.required_dimensions
        if _canonical_dimension(dimension)
    }
    for statement in composition.statements:
        if target.target_id not in statement.target_ids:
            continue
        if statement.mode not in ANSWERING_STATEMENT_MODES:
            continue
        answered = {
            _canonical_dimension(dimension)
            for dimension in statement.answered_dimensions
        }
        if not required.issubset(answered):
            continue

        by_cluster = statement_claims_by_cluster(composition, statement)
        qualifying = {
            cluster_id
            for cluster_id, claims in by_cluster.items()
            if statement_satisfies_support_policy(
                statement, claims, support_policy=target.support_policy
            )
        }

        if not by_cluster:
            # No cluster resolves at all: a genuinely claimless statement.
            # It satisfies no support policy — the same direct check a single
            # scoped cluster would get is run here, and it refuses a claimless
            # statement for every policy, ``derivation`` included, so such a
            # statement can never answer the target.
            if not statement_satisfies_support_policy(
                statement, [], support_policy=target.support_policy
            ):
                continue
        elif statement.dimension_support:
            # Attributed path: a statement derived with per-cluster dimension
            # tracking. Every required dimension must name at least one
            # cluster that itself qualifies under the target's support
            # policy — not any cluster the statement happens to resolve to.
            support_by_dimension: dict[str, list[str]] = {}
            for dimension, cluster_ids in statement.dimension_support.items():
                support_by_dimension.setdefault(
                    _canonical_dimension(dimension), []
                ).extend(cluster_ids)
            if not all(
                dimension in support_by_dimension
                and any(
                    cluster_id in qualifying
                    for cluster_id in support_by_dimension[dimension]
                )
                for dimension in required
            ):
                continue
        else:
            # Fallback path: a legacy statement (a snapshot, a fixture, or a
            # hand-built record) with no recorded attribution. Scope to the
            # clusters that actually name this target — by the resolved
            # claim's own ``target_ids``, or by the cluster registry's
            # ``target_ids`` when the composition carries one — and require
            # every one of them to qualify, not just any. A fully legacy
            # shape where nothing in scope names the target falls back to
            # every resolved cluster, which is what today's pooled check
            # already tested.
            scoped = {
                cluster_id
                for cluster_id, claims in by_cluster.items()
                if any(target.target_id in claim.target_ids for claim in claims)
                or (
                    (registered := composition.claim_clusters.get(cluster_id))
                    is not None
                    and target.target_id in registered.target_ids
                )
            }
            if not scoped:
                scoped = set(by_cluster.keys())
            if not all(cluster_id in qualifying for cluster_id in scoped):
                continue
        return statement
    return None


def target_is_answered(state: ResearchState, target: EvidenceTarget) -> bool:
    """True when one reader statement satisfies this target's obligation.

    The yes/no reading of ``answering_statement_for``: the statement it names
    is the answer, and there is no second rule for "answered" anywhere in the
    tree.
    """
    return answering_statement_for(state, target) is not None


def unanswered_required_targets(
    state: ResearchState,
    sub_topic: SubTopic | None = None,
) -> list[EvidenceTarget]:
    """The counted obligations still owing an answer, in plan order.

    Only ``required`` targets are returned. An optional target is a nice-to-
    have the plan recorded; treating one as an outstanding obligation would
    keep a finished topic eligible for repair forever, which is the unbounded
    loop the macro-iteration ceiling exists to prevent. ``sub_topic`` narrows
    the question to one topic — the Researcher asks per topic, the graph asks
    across the plan.
    """
    topics = state.sub_topics if sub_topic is None else [sub_topic]
    return [
        target
        for topic in topics
        for target in counted_evidence_targets(topic.evidence_targets)
        if target.required and not target_is_answered(state, target)
    ]


def _collapsed(value: str) -> str:
    """Whitespace-collapsed, case-folded text, for identity-free matching."""
    return " ".join(value.split()).casefold()


def _acquire_coverage_ids(state: ResearchState) -> set[str]:
    """Every planned topic a typed repair has asked to acquire for.

    Two records of one request, and both are read because they are not
    interchangeable. ``state.refinement_targets`` is the union the refinement
    hop routes over: it carries the Critic's gaps *and* the terminal semantic
    review's defects, which exist as jobs and nowhere else in the state.
    ``state.critique.gaps`` is the durable record of the Critic's own half —
    a job list is recomputed at each hop, so a topic an acquisition gap named
    before that hop stamped its jobs (a resumed snapshot, a state assembled by
    a caller) must stay selectable rather than silently dropping out.
    """
    requested = {
        job.coverage_id
        for job in state.refinement_targets
        if job.action == "acquire" and job.coverage_id
    }
    critique = state.critique
    if critique is not None:
        requested.update(
            gap.coverage_id
            for gap in critique.gaps
            if gap.coverage_id and gap.repair_action == "acquire"
        )
    return requested


def _topic_has_prior_finding(state: ResearchState, sub_topic: SubTopic) -> bool:
    """True when some raw finding names this topic — a legacy fallback only.

    It is no longer how a refinement decides a topic is done: a finding is not
    an answered obligation, and the reviewed baseline skipped topic-04,
    topic-02 and topic-05 on exactly this test while their required targets
    were unanswered. It survives for one case only — a plan carrying no
    evidence targets at all, where there is no obligation to validate against
    and "has anything ever been found for it" is the only honest signal left.
    """
    title = _collapsed(sub_topic.title)
    return any(
        _collapsed(finding.related_sub_topic) == title
        for finding in state.raw_findings
    )


def sub_topic_owes_evidence(state: ResearchState, sub_topic: SubTopic) -> bool:
    """True when a research pass could still do something for this topic.

    Two reasons, and neither is "a finding already exists somewhere":

    * the plan still owes an answer for one of the topic's required targets —
      a required target is answered only when a reader statement satisfies its
      dimensions and support policy, so one raw metadata finding leaves the
      obligation open and the topic eligible whether or not the Critic named
      it. This is what stops Critic silence from suppressing a required
      target;
    * a typed repair asked for *acquisition* at the topic — the Critic's gap,
      or a defect the terminal semantic review named, which reaches this
      predicate only as a ``refinement_target`` job (``_acquire_coverage_ids``).
      An answered topic is not exempt: a review that calls for a missing
      independent source is asking for searches, and the topic has to be
      selectable for the pass it bought to do any.

    A plan with no evidence targets has no obligation to check against — a
    legacy snapshot the plan contract says must be replanned — and there the
    only honest signal left is whether the topic ever produced a finding.

    Stated here, beside ``target_is_answered``, because it is the one reading
    of "is this topic still work" the whole tree shares: the Researcher uses it
    to choose the topics a pass researches, and the graph uses the same answer
    to decide which of their queues are work the run can still spend. Two
    readings would let a pass skip a topic while the repair loop kept counting
    that topic's leftovers as work owed.
    """
    if sub_topic.coverage_id in _acquire_coverage_ids(state):
        return True
    targets = counted_evidence_targets(sub_topic.evidence_targets)
    if not targets:
        return not _topic_has_prior_finding(state, sub_topic)
    return any(
        target.required and not target_is_answered(state, target)
        for target in targets
    )


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
        elif field_name == "progress_history":
            # Appended like a log, then bounded by the macro-iteration ceiling
            # plus the initial checkpoint below. The ceiling is a bound, not a
            # reason to refuse the snapshot: refusing one would lose the very
            # comparison the stop decision reads.
            if not isinstance(value, list):
                raise TypeError("progress_history update must be a list")
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

    if "composition" in update and "report_review" not in update:
        # Task 10: a judgement belongs to the report it judged. Replacing the
        # composition drops the stored review unless the incoming composition
        # still carries the same semantic fingerprint — the check is on
        # content, references, and targets, never on the generated quality
        # badge the terminal finalizer rewrites on the way out. A review is
        # *replaced* by whoever runs one next, and an absent review is never an
        # acceptance: it is what makes the terminal gates report `partial`.
        stored = payload.get("report_review")
        if stored is not None:
            incoming = update["composition"]
            if isinstance(incoming, dict):
                incoming = ReportComposition.model_validate(incoming)
            from deep_research.agents.report_review import (  # noqa: PLC0415
                composition_semantic_fingerprint,
            )

            fingerprint = composition_semantic_fingerprint(incoming)
            if not fingerprint or stored.get("composition_fingerprint") != fingerprint:
                payload["report_review"] = None

    # The macro-iteration ceiling plus the initial checkpoint: one snapshot per
    # pass, and the oldest are dropped so a long run's checkpoint stays bounded
    # by its own declared budget rather than by how long it ran.
    history_bound = int(payload["max_iterations"]) + 1
    if len(payload["progress_history"]) > history_bound:
        payload["progress_history"] = payload["progress_history"][-history_bound:]

    return ResearchState.model_validate(payload)


def advance_research_iteration(state: ResearchState) -> ResearchState:
    if state.iteration >= state.max_iterations:
        raise ValueError("cannot advance iteration beyond max_iterations")

    payload = state.model_dump(mode="python")
    payload["iteration"] = state.iteration + 1
    return ResearchState.model_validate(payload)
