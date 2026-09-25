"""Shared typed contracts for research state and domain data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
import re
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
Priority: TypeAlias = Annotated[int, Field(ge=1)]
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
    itself *states* a date, that date is preserved instead: a question that
    says "as of 2025-12-31" is answered as of 2025-12-31, and the contract
    says so. A year the question merely observes is not a cutoff: it is the
    period the answer is about, named in ``evidence_period_requirement``,
    while ``as_of_date`` stays the clock's — so later revisions and published
    outcomes stay admissible beside the forecasts.

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


FigureKind: TypeAlias = Literal["actual", "forecast"]
UnitDimension: TypeAlias = Literal["power", "energy", "percent"]
MAX_SNIPPET_CHARS = 600
# Is a number a figure in one of the two bases a run measures in? The
# vocabulary is shared so the Researcher, which decides whether a passage
# states a figure in a target's own unit, and every later reader of a unit
# label cannot drift apart.
_POWER_UNIT = re.compile(r"\b(?:[kmg]w|(?:kilo|mega|giga)watts?)\b", re.I)
_ENERGY_UNIT = re.compile(
    r"\b(?:[kmg]wh|(?:kilo|mega|giga)watt[\s-]?hours?)\b", re.I
)


class FindingFigure(ContractModel):
    """One figure a finding states, exactly as its snippet writes it (spec §4)."""

    value: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    period: str | None = None
    kind: FigureKind | None = None


# The Evidence Verifier's own vocabulary (spec §5). ``FigureAttribution`` is
# who a kept figure credits; ``FindingStatus`` is the finding's outcome; the
# two drop-reason aliases name why a figure or a whole finding did not
# survive, so a dropped record always carries a reason a reader can print.
FigureAttribution: TypeAlias = Literal["own", "relayed", "unattributed"]
FindingStatus: TypeAlias = Literal["verified", "verified_corrected", "dropped"]
FigureDropReason: TypeAlias = Literal[
    "evidence_not_on_page",     # §5.2: evidence_words are not in the read
    "correction_not_on_page",   # §5.2: corrected period or scope not in evidence_words or passage
    "context_rejected",         # §5.2: the Context Check said reject
    "context_unavailable",      # §5.2: no reply for the figure, and its snippet does not state it
]
FindingDropReason: TypeAlias = Literal[
    "read_not_found", "snippet_not_on_page", "all_figures_dropped"
]


class FigureContext(ContractModel):
    """The context a Context Check confirmed for one kept figure (spec §5.2)."""

    period: str | None = None
    scope: str | None = None
    attribution: FigureAttribution
    organisation: str = Field(min_length=1)
    """``own``: the publisher; ``relayed``: the originator; ``unattributed``: the page's owner (host)."""
    kind: FigureKind


class FigureResult(ContractModel):
    """The Figure Match and Context Check outcome for one finding figure."""

    figure: FindingFigure
    matched: bool
    """Figure Match's verdict (spec §5.1 step 2)."""
    context: FigureContext | None = None
    """Set on every kept figure."""
    evidence_words: str | None = None
    corrected: bool = False
    dropped_reason: FigureDropReason | None = None
    reason: str | None = None
    """The Context Check's own reason text."""

    @property
    def kept(self) -> bool:
        return self.dropped_reason is None

    @model_validator(mode="after")
    def kept_figures_carry_context(self) -> "FigureResult":
        if self.kept and self.context is None:
            raise ValueError("a kept figure carries its verified context")
        return self


class FindingVerification(ContractModel):
    """The Evidence Verifier's judgement of one finding, and its figures."""

    status: FindingStatus
    figure_results: list[FigureResult] = Field(default_factory=list)
    dropped_reason: FindingDropReason | None = None
    context_unchecked: bool = False

    @model_validator(mode="after")
    def status_is_consistent(self) -> "FindingVerification":
        if (self.status == "dropped") != (self.dropped_reason is not None):
            raise ValueError(
                "a dropped finding, and only a dropped one, names its reason"
            )
        if self.status != "dropped" and self.figure_results and not any(
            result.kept for result in self.figure_results
        ):
            raise ValueError("a verified finding keeps at least one figure")
        if self.status == "verified" and any(
            result.corrected or not result.kept for result in self.figure_results
        ):
            raise ValueError(
                "a corrected or dropped figure makes the finding verified_corrected"
            )
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
    attributed_issuer: str | None = None
    """The body this page ATTRIBUTES the figure to, not the body that served it.

    A relay is a page whose own words hand the figure to somebody else — the
    audited run's cleanedge.com dive says "according to the U.S. Energy
    Information Administration (EIA)" beside EIA's 10.3 GW and 18.2 GW. Empty
    means the page states the figure as its own publisher's, which is the
    ``source_url``'s publisher and needs no second name. Credit is what this
    field exists to get right: read as the host, a relay's copy of one
    measurement becomes a second, independent one, and the report prints a
    conflict where the evidence holds one figure published twice.
    """
    attribution_quote: str | None = None
    """The page's own words that attribute the figure to ``attributed_issuer``.

    Admitted only when the read the finding cites carries the phrase verbatim
    in the excerpt's own passage or its immediate neighbour, never merely
    somewhere else in the document, and only when the phrase both names that
    body and carries a recognized attribution cue beside the name —
    "according to", "reported by", a possessive, or similar — so the two
    fields are one claim recorded in two halves: naming a body is not
    crediting it with anything, and a page that merely mentions or contrasts
    itself with a body — "Unlike the EIA, our survey found..." — attributes
    nothing to it. An attribution the read does not carry this way is not a
    fact about the page, and both fields are then recorded empty. Shaped
    after ``TemporalClaim``'s value/quote pair for the same reason — a name
    without its phrase is the model's reading of a document it is quoting.
    """
    measure_scope: str | None = None
    """The segment or basis the figure covers, in the source's own words.

    "all segments" for the market monitor's 12,314 MW, "grid-scale" for its
    2025 forecast: two figures of one market on two bases, so a target asking
    for one of them is answered by that one alone. Admitted only when the
    read's own text carries this exact wording, the same containment an
    excerpt is admitted with; empty means the page states no scope, or the
    model's proposed wording is not the read's, which is not the same as the
    scope being the target's.
    """
    release_date: str | None = None
    """The date the ATTRIBUTED body released the figure, as the page writes it.

    Distinct from ``statement_date``, which is when the page carrying the
    figure states it: on a relay the two differ, and a reader dating a figure
    needs the release the figure belongs to rather than the copy's own day.
    Admitted only when the read's own text states this value, at this
    precision or a finer one — the same verification every other date in
    this project passes. Empty when the page states no release date, or
    states one the model's proposed value does not match, and then
    ``statement_date`` is the only date recorded.
    """
    snippet: str | None = None
    """The verbatim admitted passage text this finding rests on, from the researcher's admitted passage.

    At most ``MAX_SNIPPET_CHARS`` characters, recorded exactly as the source
    writes it — never paraphrased. ``None`` for a finding extracted before
    evidence snippets were captured, or whose extraction wrote no matching
    text.
    """
    read_id: str | None = None
    """The read ``snippet`` was found on, from the researcher's admitted passage."""
    locator: str | None = None
    """The passage id within ``read_id`` that carries ``snippet``, from the researcher's admitted passage."""
    figures: list[FindingFigure] = Field(default_factory=list)
    """Every figure ``snippet`` states, from the researcher's admitted passage."""
    verification: FindingVerification | None = None
    """``None`` until the Evidence Verifier has judged this finding."""

    @model_validator(mode="after")
    def normalize_binding_and_dates(self) -> "Finding":
        """Keep first-seen target order, and treat a blank date as absent.

        Both are what a consumer compares against: a repeated target id would
        make one binding look like two, and an empty string would sort as a
        date a reader could rank against a real one.
        """
        self.target_ids = list(dict.fromkeys(self.target_ids))
        for name in (
            "vintage",
            "statement_date",
            "data_period",
            "attributed_issuer",
            "attribution_quote",
            "measure_scope",
            "release_date",
            "snippet",
            "read_id",
            "locator",
        ):
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



# The versioned evidence contract this build writes, and the value a snapshot
# written before it carries. Legacy snapshots keep reading: they load with the
# legacy version and empty registries, so no read provenance is ever
# synthesized for a run that recorded none, and a consumer can tell a
# pre-contract snapshot from a current-contract one instead of assuming.
# Contract 2 is the Evidence Verifier's record (spec §6.2): findings, facts, refusals.
QUALITY_CONTRACT_VERSION = "2"
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

    Carries no support policy (PD-16): step 4 removes the pair and
    support-policy fields along with the Fact Checker, so an obligation is
    answered on the answer-side facts alone — the statement names the target,
    asserts something, and fills the fields the target declares.
    """

    target_id: str = Field(min_length=1)
    coverage_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    required: bool
    measure: str = Field(min_length=1)
    """The obligation's measured quantity, as the plan states it ("battery storage power capacity added")."""
    unit_dimension: UnitDimension | None = None
    """The figure's physical dimension, or ``None`` for a qualitative target."""
    period: str | None = None
    """The period the target asks about ("2024")."""
    kind: FigureKind | None = None
    """Whether the target asks for an actual or a forecast figure."""
    geography: str | None = None
    """The geography the target asks about ("United States")."""
    organisation: str | None = None
    """The organisation the target asks about, or ``None`` if any organisation may answer."""


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
    know stays readable rather than failing validation.
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


class ReportQualitySnapshot(ContractModel):
    """Deterministic integrity and coverage metrics for one report pass.

    Every field here is a *structural* diagnostic: a count, a ratio, or an
    enumerated hard-failure name derived from typed state and the composition.
    Nothing in this model is a reader-quality judgement, which is why the
    terminal semantic review is stored on its own (``semantic_review_status``,
    ``semantic_review_score``) rather than folded into ``hard_failures``:
    a structural clean bill of health and a semantic acceptance are two
    different claims, and the reviewed baseline showed a report can hold the
    first while failing the second.
    """

    # Every reading below carries a zero so a partially measured pass is
    # representable as incomplete rather than unconstructible. The bounds are
    # unchanged.
    cited_sources: int = Field(default=0, ge=0)
    uncited_settled_points: int = Field(default=0, ge=0)
    hard_failures: list[str] = Field(default_factory=list)
    # --- Task 10: the semantic judgement, kept apart from the counts --------
    semantic_review_status: str = ""
    """``scored`` / ``incomplete`` / ``provider_failed``, or ``""`` for none.

    Deliberately not a ``hard_failure``: a review that could not be made is an
    absent judgement, and the hard-failure list is a closed set of defects
    found in the report.
    """
    semantic_review_score: float | None = None
    """The review's mean over the seven dimensions, or ``None`` for no score."""
    semantic_review_fingerprint: str = ""
    """The exact packet fingerprint the stored judgement was made over."""
    # --- Step 4 (Task 4.1): what the Evidence Verifier pipeline reads -------
    # One reading per reader-visible property of a finished pass, filled by
    # the quality pass from the verified findings and the composition, and
    # gated by Task 4.3. ``unaccounted_target_ids``, ``cited_sources``,
    # ``uncited_settled_points`` and ``hard_failures`` already exist above and
    # are only added to by this step.
    required_target_ids: list[str] = Field(default_factory=list)
    answered_target_ids: list[str] = Field(default_factory=list)
    missing_required_target_ids: list[str] = Field(default_factory=list)
    unaccounted_target_ids: list[str] = Field(default_factory=list)
    """Required targets with neither an answer nor a recorded reason."""
    verified_findings: int = Field(default=0, ge=0)
    corrected_findings: int = Field(default=0, ge=0)
    dropped_findings: int = Field(default=0, ge=0)
    context_unchecked_findings: int = Field(default=0, ge=0)
    dropped_figures: int = Field(default=0, ge=0)
    cited_findings: int = Field(default=0, ge=0)
    duplicate_fact_rows: int = Field(default=0, ge=0)
    """An invariant, not a warning: ``fact_rows()`` already merges (PD-10)."""
    unresolved_citations: int = Field(default=0, ge=0)
    unjudged_sentences: list[str] = Field(default_factory=list)
    """``"S003"``: kept with no verdict and no batch failure to blame."""
    refused_sentences: int = Field(default=0, ge=0)
    forecasts_without_release: int = Field(default=0, ge=0)
    """PD-24: counted and printed, never a gate."""


# --- Task 8: the typed defect vocabulary ------------------------------------
#
# Ten kinds, and the set is *normative*: the kind names what is wrong rather
# than how to fix it. A kind outside this list is a schema failure, not a new
# category — a free-text kind would make "which defects does this system
# find?" unanswerable.
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

#: The reserved ``target_ids`` entry that scopes a defect to the whole answer.
#:
#: An original-question omission has no planned target to name — that is what
#: makes it an omission — so it points at the question instead of inventing a
#: topic id. It is also the honest scope of a legacy gap that named no plan id
#: at all, because such a gap was only ever listed when closing it would change
#: the answer to the question.
QUESTION_TARGET_ID = "question"


# --- Step 4 (Task 4.1): the Report Reviewer's typed defects -----------------
#
# The shape the terminal review returns from step 4 on. It keeps the gap
# vocabulary (``GapKind``, ``GapSeverity``, ``GAP_MATERIAL_SEVERITIES``)
# because the kinds name what is wrong. ``target_ids`` is what makes a defect
# routable (Task 4.4's targeted extra pass); ``statement_ids`` is what ties it
# to the sentence it judges, so an unsettled statement can be named by a
# defect.
class ReviewDefect(ContractModel):
    """One report problem the terminal review returns, with its scope."""

    defect_id: str = Field(min_length=1)    # "review-01"
    kind: GapKind
    severity: GapSeverity
    target_ids: list[str] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    problem: str = Field(min_length=1)

    @property
    def material(self) -> bool:
        """True when this defect must be closed before the report is accepted."""
        return self.severity in GAP_MATERIAL_SEVERITIES


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

REVIEW_RUBRIC_VERSION = 3
"""Which semantic rubric a review was made under.

Version 1 is the structural formula (``judge_whole_report``); version 2 is the
seven semantic definitions; version 3 is the step-4 review, which judges every
statement it was given against the verified findings behind it and returns
typed defects (spec §6.2). The version travels on the record so a historical
diagnostic and a semantic judgement can never be compared as if they were the
same measurement.
"""

# How one review ended. Deliberately its own vocabulary: a semantic review
# that could not be completed is not the same fact as a report that was never
# judged at all, and collapsing the two is how "no judgement" starts reading
# as a verdict.
ReportReviewStatus: TypeAlias = Literal["scored", "incomplete", "provider_failed"]
REPORT_REVIEW_STATUSES: tuple[ReportReviewStatus, ...] = (
    "scored",
    "incomplete",
    "provider_failed",
)

# What one review concluded about one reader statement, in the step-4
# vocabulary: ``supported`` is a statement the verified findings carry as
# written, ``unsupported`` is one they do not, and ``not_reviewed`` is the
# honest default for a statement the review never reached. The claim-era
# ``attributed``/``inference``/``returned_to_fact_checker`` dispositions left
# with the Fact Checker (PD-16, D2): nothing asks a statement for a pair, an
# attribution badge, or a return trip any more.
StatementReviewDisposition: TypeAlias = Literal[
    "supported",
    "unsupported",
    "not_reviewed",
]
STATEMENT_REVIEW_DISPOSITIONS: tuple[StatementReviewDisposition, ...] = (
    "supported",
    "unsupported",
    "not_reviewed",
)

UNREVIEWED_STATEMENT_DISPOSITION: StatementReviewDisposition = "not_reviewed"
"""The disposition of a statement the review never reached.

The honest default, and never a pass: a scored review may not carry one, so
missing coverage cannot be recorded as a clean result.
"""

UNSETTLED_STATEMENT_DISPOSITIONS: frozenset[str] = frozenset(
    {"unsupported", UNREVIEWED_STATEMENT_DISPOSITION}
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
    is one that saw the whole report, covered every reader statement it was
    given, and returned exactly the seven dimensions over a fingerprint that
    still matches the packet: the model validators below refuse the
    combination that would let a partial review claim a score. ``incomplete``
    and ``provider_failed`` carry no score at all —
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
    defects: list[ReviewDefect] = Field(default_factory=list)
    per_statement_dispositions: dict[str, StatementReviewDisposition] = Field(
        default_factory=dict
    )
    reviewed_statement_ids: list[str] = Field(default_factory=list)
    unreviewed_statement_ids: list[str] = Field(default_factory=list)
    derived_defect_statement_ids: list[str] = Field(default_factory=list)
    """Statements whose material defect this project derived from a disposition."""
    missing_required_target_ids: list[str] = Field(default_factory=list)
    """Required targets with no answer, stamped by code rather than asked of the model (PD-5)."""
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
        """True when the reviewer read every reader statement it was given."""
        return not self.unreviewed_statement_ids

    @property
    def material_defects(self) -> list[ReviewDefect]:
        """The defects that must be closed before the report may be accepted."""
        return [defect for defect in self.defects if defect.material]

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


class ReportStatement(ContractModel):
    """One reader statement, with the evidence mapping that makes it auditable.

    Every sentence the reader report prints is one of these: the text, the
    verified findings it rests on, and the planned targets it answers. The
    written pass builds one per printed point, which is what keeps "factual
    prose outside this mapping" a validation failure rather than a style
    problem.
    """

    statement_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)


class ReportPoint(ContractModel):
    """One printed statement, with the sources it rests on.

    ``statement`` is the record that makes the point auditable — the text's
    own id, the verified findings behind it and the targets it answers. The
    written pass always fills it, so a rendered point always has one.
    """

    text: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)
    statement: ReportStatement | None = None

    @property
    def statement_id(self) -> str:
        return self.statement.statement_id if self.statement else ""


class ReportSection(ContractModel):
    """One validated theme of the findings, as printed points."""

    title: str = Field(min_length=1)
    points: list[ReportPoint] = Field(default_factory=list)


class ReportTerminalState(ContractModel):
    """What a run's own terminal checks decided, as the reader meets them.

    Written once, by the terminal finalizer, from the records the run's own
    gates and reviewers wrote: the status the router ended on, the semantic
    review's status and score, and the target coverage the acceptance gates
    measured. It exists because every one of those facts was already in state
    and none of them reached the reader: a report whose run failed and whose
    review was never scored was published saying only ``Quality status:
    partial``.

    Deliberately *not* part of a composition the writer builds, and
    deliberately not in ``composition_semantic_fingerprint``: this describes
    the run, not the report's content, so stamping it at publication can never
    invalidate the judgement the terminal review recorded from the same
    content. Every field is project-generated — an enumerated status, an
    integer count, or a gate's own hard-failure name — so nothing here can
    carry provider text into a published artifact.
    """

    status: str = ""
    """The route the run ended on: ``completed`` / ``failed`` /
    ``max_iterations`` / any other ``graph_status`` name, or ``""`` when no
    terminal record was stamped."""
    review_status: str = ""
    """The semantic review's status — ``scored`` / ``incomplete`` /
    ``provider_failed``, or ``""`` when none was recorded."""
    review_score: float | None = None
    """The semantic review's mean score when it was scored, else ``None``.
    Carried so the reader sees the judgement, not only that one was made."""
    required_targets: int = Field(default=0, ge=0)
    answered_targets: int = Field(default=0, ge=0)
    gate_failures: list[str] = Field(default_factory=list)
    """The typed names of the acceptance gates that failed this report."""


class RejectedDraftPoint(ContractModel):
    """One drafted point this pass could not print, kept in full.

    ``ReportComposition.rejected`` carries the terse, project-generated
    reason each refusal earned; this is its un-truncated companion — the
    full drafted text, with the finding labels it cited, keyed by ``where``,
    so a reader of the evidence ledger or the quality record can see exactly
    which drafted point tripped which reason, without replaying the provider
    call that wrote it.
    """

    where: str = Field(min_length=1)
    text: str = ""
    reason: str = Field(min_length=1)
    finding_labels: list[str] = Field(default_factory=list)


class EarlierEdition(ContractModel):
    """One earlier reported value of a series a fact row updates."""

    value: str               # "10.3 GW", as written
    release: str | None      # the earlier finding's release text
    finding_id: str


class FactRow(ContractModel):
    """One verified figure as the reader's Key Facts table prints it."""

    row_id: str                         # "K001"
    organisation: str
    attribution: FigureAttribution
    relay_host: str | None = None       # the relaying site when attribution == "relayed"
    measure: str                        # the answered target's measure, else the unit label
    period: str | None = None
    value: str                          # "10.4 GW", as written
    kind: FigureKind
    scope: str | None = None
    release: str | None = None
    finding_id: str                     # the cited finding
    duplicate_finding_ids: list[str] = Field(default_factory=list)
    earlier: list[EarlierEdition] = Field(default_factory=list)
    target_ids: list[str] = Field(default_factory=list)
    context_unchecked: bool = False


class NotFoundTarget(ContractModel):
    """One required target no acquisition path answered.

    Feeds the reader report's Not found section, so a missing obligation
    is stated rather than silently absent from the Key Facts table.
    """

    target_id: str
    question: str
    queries: list[str] = Field(default_factory=list)      # the sub-topic's planned queries
    pages_read: list[str] = Field(default_factory=list)   # URLs its acquisition read
    searched: bool = False                                 # the acquisition ran at all


class ReportComposition(ContractModel):
    """Everything one written pass composed, and the evidence it renders.

    Built once per pass by the Report Writer and handed to the renderers and
    the terminal gates, so they can never disagree about the same pass.
    ``sources`` are canonicalized on construction, which is what makes "one
    row per canonical record" a property of the type rather than of the
    caller.

    It lives here, beside the rest of the research state, because
    ``ResearchState`` carries the exact composition the terminal quality pass
    judged. The canonicalization helper is imported inside the validator
    rather than at module scope: ``agents.identity`` imports this module, so
    a module-level import would be a cycle, and a validator only ever runs
    once every module is loaded.

    ``answer_kind``, ``date_basis`` and ``requested_word_limit`` are the
    frozen contract's values, copied here so a renderer never has to
    re-derive them; ``generated_on`` is the run clock's own date, which is
    not the contract's answer date. All of them are optional so a fixture or
    a snapshot written before this contract still composes.
    """

    question: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    max_extra_passes: int = Field(default=0, ge=0)
    """The extra-pass ceiling this pass was composed under, not the pass count."""
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
    sources: list[ScoredSource] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    fact_rows: list[FactRow] = Field(default_factory=list)
    """Every verified figure, one row per Key Facts table entry."""
    not_found: list[NotFoundTarget] = Field(default_factory=list)
    """Every required target no acquisition path answered."""
    finding_labels: dict[str, str] = Field(default_factory=dict)
    """Each printed finding label mapped to the finding id it cites."""
    statement_verdicts: dict[str, str] = Field(default_factory=dict)
    """Statement id to ``"consistent"`` / ``"corrected"`` / ``"unchecked"``.

    The Statement Check's verdict on every sentence this pass drafted, with
    ``"unchecked"`` for a sentence whose batch failed (spec §5.4). Filled by
    the Report Writer as it keeps, corrects, or refuses each sentence, and
    gated on by the quality pass (Task 4.3, PD-10).
    """
    limitations: list[str] = Field(default_factory=list)
    errors: list[ResearchError] = Field(default_factory=list)
    summary: list[ReportPoint] = Field(default_factory=list)
    sections: list[ReportSection] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    """Drafted content this pass refused, as project-generated reasons."""
    rejected_points: list[RejectedDraftPoint] = Field(default_factory=list)
    """The same refusals, in full: the drafted text and the labels it cited."""
    answer_kind: AnswerKind | None = None
    """The frozen answer form, or ``None`` for a legacy composition."""
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
    terminal: ReportTerminalState = Field(default_factory=ReportTerminalState)
    """What the run's terminal checks decided, stamped at publication.

    Empty for a composition nobody finalized — a fixture, or a pass the
    Synthesizer composed but no finalizer published — and a renderer states
    nothing about checks it was not told about.
    """

    @model_validator(mode="after")
    def canonicalize_evidence(self) -> ReportComposition:
        from deep_research.agents.identity import (  # noqa: PLC0415
            merge_source_snapshot,
        )

        self.sources = merge_source_snapshot([], self.sources)
        return self

    @property
    def statements(self) -> list[ReportStatement]:
        """Every statement this composition renders, in render order, once each.

        One record per statement id: a point printed in two places is one
        statement the reader meets twice, and a *list of statements* that
        carried it twice would print two rows for one id in the ledger's
        statement map and ask the semantic reviewer, which judges every
        statement here, to judge it twice. First occurrence wins, so the order
        stays the reader's.
        """
        rows: list[ReportStatement] = []
        for point in self.summary:
            if point.statement is not None:
                rows.append(point.statement)
        for section in self.sections:
            for point in section.points:
                if point.statement is not None:
                    rows.append(point.statement)
        unique: list[ReportStatement] = []
        seen: set[str] = set()
        for statement in rows:
            if statement.statement_id in seen:
                continue
            seen.add(statement.statement_id)
            unique.append(statement)
        return unique


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
    verified_findings: list[Finding] = Field(default_factory=list)
    """The complete verified snapshot for the run so far.

    Written by the Evidence Verifier and replaced on each write, the same
    way ``evaluated_sources`` is: this is the full current picture, never
    an append-only log.
    """
    evaluated_sources: list[ScoredSource] = Field(default_factory=list)
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
    report_review: ReportReview | None = None
    """The terminal semantic judgement of ``report``, or ``None``.

    Replaced only by a review of the same semantic input fingerprint: a
    composition whose content, references, or targets changed is a different
    report, and a judgement of the old one cannot vouch for it. The quality
    snapshot's ``semantic_review_*`` fields are filled from this record, so a
    consumer that reads only the snapshot still sees which judgement stood —
    and sees that there was none.
    """
    run_telemetry: RunTelemetry | None = None
    """The run's §7.3 telemetry, stamped at publication, or ``None``.

    The collector's own snapshot, taken by the terminal finalizer from the one
    collector ``build_runtime`` created for the run, so it covers every
    provider call of the run rather than one pass's. Replaced on every write
    and never appended: it is the reading of *this* run, and a stale reading
    beside a fresh one would double-count the peak.

    ``None`` means the run carried no collector — a harness that built its
    providers directly, or a snapshot written before there was one. That is
    the honest value: an empty snapshot would publish a measured idle run in
    place of a run nothing measured.
    """
    iteration: int = Field(default=0, ge=0)
    max_extra_passes: int = Field(default=1, ge=0)
    """How many extra research passes this run may still buy (D4, §6.5).

    One by default: an extra pass runs only when required targets are still
    missing, and only for those targets. ``graph.max_extra_passes`` supplies
    the configured value.
    """
    extra_pass_target_ids: list[str] = Field(default_factory=list)
    """The required targets the next extra pass is confined to, or ``[]``.

    Replaced on every write, never appended: this is the current pass's job
    list, so a target an earlier pass owed but the newest decision does not
    name cannot keep the run alive.
    """
    memory_context: MemorySnapshot = Field(default_factory=MemorySnapshot)
    events: list[ResearchEvent] = Field(default_factory=list)
    errors: list[ResearchError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_iteration_bounds(self) -> ResearchState:
        if self.iteration > self.max_extra_passes:
            raise ValueError("iteration cannot exceed max_extra_passes")
        return self


class ResearchStateUpdate(TypedDict, total=False):
    session_id: str
    original_question: str
    answer_contract: AnswerContract | None
    sub_topics: list[SubTopic]
    initial_target_ids: list[str]
    expanded_target_ids: list[str]
    raw_findings: list[Finding]
    verified_findings: list[Finding]
    evaluated_sources: list[ScoredSource]
    report: str | None
    report_path: str | None
    report_evidence: str | None
    evidence_path: str | None
    composition: ReportComposition | None
    quality: ReportQualitySnapshot | None
    unique_source_count: int
    read_records: dict[str, ReadRecord]
    evidence_units: dict[str, EvidenceUnit]
    evidence_dispositions: list[EvidenceDisposition]
    boundary_audits: dict[str, BoundaryAudit]
    acquisition_state_by_target: dict[str, AcquisitionState]
    quality_contract_version: str
    report_review: ReportReview | None
    run_telemetry: RunTelemetry | None
    max_extra_passes: int
    extra_pass_target_ids: list[str]
    memory_context: MemorySnapshot
    events: list[ResearchEvent]
    errors: list[ResearchError]



# Fields whose update is a delta appended to what the state already holds.
# ``evaluated_sources`` and ``verified_findings`` are deliberately absent: each
# carries the complete canonical snapshot for the run so far, which its
# producer — Source Evaluator or Evidence Verifier — merges from
# ``deep_research.agents.identity`` before writing. Appending them instead
# stored one more copy of every source and finding per research pass.
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
            from deep_research.agents.report_reviewer import (  # noqa: PLC0415
                composition_semantic_fingerprint,
            )

            fingerprint = composition_semantic_fingerprint(incoming)
            if not fingerprint or stored.get("composition_fingerprint") != fingerprint:
                payload["report_review"] = None

    return ResearchState.model_validate(payload)


def advance_research_iteration(state: ResearchState) -> ResearchState:
    if state.iteration >= state.max_extra_passes:
        raise ValueError("cannot advance iteration beyond max_extra_passes")

    payload = state.model_dump(mode="python")
    payload["iteration"] = state.iteration + 1
    return ResearchState.model_validate(payload)


# --- Task 4.14: run telemetry (spec 7.3) -------------------------------------
#
# What one run spent: its rate-limit errors, its peak number of provider calls
# in flight, each stage's calls and seconds, and each operation's output tokens
# against the cap that bounds it. These are measurements, never levers: nothing
# in the run reads them back to change a cap or a concurrency limit (§7.3, §12),
# and the advice lines rendered from them are for the operator between runs.
#
# The models live here rather than beside their collector because
# ``observability`` already imports this module: importing the collector's types
# from ``utils.types`` would close a cycle.


class OperationTelemetry(ContractModel):
    """One agent operation's output tokens against the cap that bounds it.

    ``max_output_tokens`` is the largest single reply of that operation, and
    ``configured_cap`` is the cap the call that produced it was configured
    with -- not a re-read of the config, which may since have changed.
    ``truncations`` counts the replies that hit that cap (the provider's
    output-limit errors), and ``cap_key`` names the config key that bounds the
    operation, so an operator knows exactly what to raise.
    """

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_default=True, frozen=True
    )

    agent: str = Field(min_length=1)
    max_output_tokens: int = Field(default=0, ge=0)
    configured_cap: int = Field(ge=1)
    truncations: int = Field(default=0, ge=0)
    cap_key: str = Field(min_length=1)


class StageTelemetry(ContractModel):
    """One agent's provider calls in a run: how many, how long, and their caps.

    ``seconds`` is the total wall time of those calls and ``slowest_seconds``
    the longest one, which is the number a concurrent stage's runtime turns on.
    """

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_default=True, frozen=True
    )

    agent: str = Field(min_length=1)
    calls: int = Field(ge=0)
    seconds: float = Field(ge=0.0)
    slowest_seconds: float = Field(ge=0.0)
    operations: tuple[OperationTelemetry, ...] = ()


class RunTelemetry(ContractModel):
    """One run's §7.3 telemetry, as the quality record and the CLI report it."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_default=True, frozen=True
    )

    rate_limit_errors: int = Field(default=0, ge=0)
    rate_limit_recovered: int = Field(default=0, ge=0)
    peak_calls_in_flight: int = Field(default=0, ge=0)
    peak_agent: str | None = Field(default=None, min_length=1)
    stages: tuple[StageTelemetry, ...] = ()
