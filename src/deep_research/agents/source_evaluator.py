"""The Source Evaluator: score every source the findings actually used.

Like the Planner and the Researcher, this module never sends a domain type
to the provider. ``SourceScoreDraft`` mirrors the model-judged part of
``ScoredSource`` with plain field types so it survives strict JSON schema
conversion, and this module stamps the parts the model must not be trusted
to supply: the canonical URL, the weighted overall score, and the
low-confidence flag.

Score convention: every score is a ``UnitScore`` in ``[0.0, 1.0]``, higher
is better, matching ``Finding.confidence`` and
``SourceReputation.reputation_score``. ``overall_score`` is a convex
combination of the three quality dimensions. Sources that were not evaluated
carry ``None`` for every quality score and an explicit ``evaluation_status``
instead of a fabricated floor.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple, Protocol

from pydantic import Field

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.evidence import (
    ReadDossier,
    ReadIdentityRequest,
    TemporalClaim,
    build_read_dossiers,
    read_serving_host,
    rejected_anchor_names,
    resolve_read_identities,
    resolve_source_identities,
    validate_metadata_anchors,
    validated_self_interest,
    validated_source_role,
    validated_temporal,
    validated_transport_relation,
)
from deep_research.agents.identity import merge_source_snapshot
from deep_research.agents.prompts import (
    SOURCE_EVALUATOR_SYSTEM_PROMPT,
    SOURCE_SCORING_INSTRUCTION,
    AgentTask,
    render_read_dossier,
    render_source_dossier,
    render_structured_reply_format,
)
from deep_research.agents.sources import (
    SourceGroup,
    group_findings_by_url,
    normalize_source_url,
)
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.memory.entries import SourceReputation
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, ProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig, EffectiveModelConfig
from deep_research.utils.types import (
    ContractModel,
    ReadRecord,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
    SubTopic,
)

SOURCE_EVALUATOR_NAME = "source_evaluator"

# Weights form a convex combination: overall_score is in [0, 1] whenever
# its three inputs are. Authority and relevance dominate because a source
# that is neither authoritative nor on-topic is not rescued by being new.
AUTHORITY_WEIGHT = 0.45
RECENCY_WEIGHT = 0.15
RELEVANCE_WEIGHT = 0.40

# How much a reputation recalled from long-term memory moves the model's
# authority judgement. Blended into authority rather than into the overall
# score so that overall_score stays a pure function of the three recorded
# dimensions.
REPUTATION_BLEND = 0.4

LOW_CONFIDENCE_THRESHOLD = 0.4
DEFAULT_BATCH_SIZE = 12
DEFAULT_MAX_TOTAL_SOURCES = 36
# Compatibility alias for callers that imported the old cap constant. The
# old single-pass cap is now represented by ``max_total_sources``.
DEFAULT_MAX_SOURCES = DEFAULT_MAX_TOTAL_SOURCES
DEFAULT_EXCERPT_CHARS = 600
_RATIONALE_CHARS = 400

# Enumerated, project-generated reasons a source was recorded without a
# model judgement. Never provider text: these strings reach prompts,
# ResearchError.details, and user-facing rationales.
FALLBACK_REASONS = {
    "unscored_provider": "The scoring model could not be reached.",
    "unscored_missing": "The scoring model returned no score for this source.",
    "unscored_cap": "This source fell past this run's scoring cap.",
    # Accept the pre-status names for callers that construct fallback records
    # directly; all emitted records use the explicit status names above.
    "model_unavailable": "The scoring model could not be reached.",
    "not_scored_by_model": "The scoring model returned no score for this source.",
    "over_source_cap": "This source fell past this run's scoring cap.",
}


class SourceScoreDraft(ContractModel):
    """One model-judged source score, before domain validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema. ``overall_score``, ``evaluation_status``, and
    ``low_confidence`` are deliberately absent — this project computes those,
    not the model.

    The fitness fields beyond the three scores are all optional, and an
    omitted field is recorded as ``unknown`` rather than defaulted to a
    judgement the model never made. The metadata anchors (``issuer``,
    ``doi``, ``year``, ``report_number``, and the ``derived_from`` list of
    DOIs or report numbers the document says its data come from) are
    proposals: each is accepted only when the read the model was shown
    actually evidences it. The four temporal fields are quoted claims — a
    value *and* the document's own words for it — and a field the document
    does not state is ``null`` rather than a date the model inferred.
    """

    url: str
    authority_score: float
    recency_score: float
    relevance_score: float
    rationale: str
    methods_score: float | None = None
    source_role: str = ""
    transport_relation: str = ""
    self_interest: str = ""
    publication_date: TemporalClaim | None = None
    data_period: TemporalClaim | None = None
    forecast_horizon: TemporalClaim | None = None
    effective_date: TemporalClaim | None = None
    freshness_status: str = ""
    issuer: str = ""
    doi: str = ""
    year: str = ""
    report_number: str = ""
    derived_from: list[str] = []


class SourceScoresDraft(ContractModel):
    """The provider-facing scoring schema for one evaluation pass."""

    sources: list[SourceScoreDraft]


# Two examples, because scoring chooses a value on a continuous scale: the
# weak and strong cases are the opposite ends of the same 0.0-1.0 direction,
# and each is internally consistent with its own synthetic dossier. Both are
# complete, so the model sees every field the record needs — including that a
# document which states nothing gets ``unknown``, empty anchors, and ``null``
# rather than an invented publisher or date.
_SOURCE_SCORE_REPLY_EXAMPLES = (
    (
        "Weak example input: an anonymous, undated post at "
        "https://weak.example.test/post only mentions the topic.",
        '{"sources":[{"url":"https://weak.example.test/post",'
        '"authority_score":0.1,"recency_score":0.5,"relevance_score":0.2,'
        '"methods_score":null,"source_role":"unknown",'
        '"transport_relation":"unknown","self_interest":"unknown",'
        '"publication_date":null,"data_period":null,"forecast_horizon":null,'
        '"effective_date":null,"freshness_status":"unknown","issuer":"",'
        '"doi":"","year":"","report_number":"","derived_from":[],'
        '"rationale":"The publisher is unidentified, there is no dating '
        'signal, and the excerpt only mentions the topic."}]}',
    ),
    (
        "Strong example input: a current primary standard at "
        "https://strong.example.test/standard directly answers the topic.",
        '{"sources":[{"url":"https://strong.example.test/standard",'
        '"authority_score":0.95,"recency_score":0.9,"relevance_score":0.95,'
        '"methods_score":0.9,"source_role":"original_report",'
        '"transport_relation":"original","self_interest":"none",'
        '"publication_date":{"value":"2026-01-15","quote":"Published by '
        'Example Standards Body on 2026-01-15."},'
        '"data_period":{"value":"2024","quote":"Observed data cover 2024."},'
        '"forecast_horizon":null,"effective_date":null,"freshness_status":'
        '"current","issuer":"Example Standards Body",'
        '"doi":"10.1234/standard.2026","year":"2026","report_number":"",'
        '"derived_from":[],'
        '"rationale":"A current standards body publication directly answers '
        'the topic with primary material."}]}',
    ),
)


class EvaluatedSources(ContractModel):
    """The validated scores ``SourceEvaluatorAgent`` produces.

    Never sent to the provider — ``SourceScoresDraft`` is. Do not route
    this agent through ``complete_output``.
    """

    sources: list[ScoredSource] = []


def clamp_unit(value: float) -> float:
    """Pin a model-supplied number into ``[0.0, 1.0]``.

    ``ScoredSource`` fields are ``UnitScore`` and would raise on an
    out-of-range value. A model that returns 9.0 for authority is making a
    formatting mistake, not invalidating the whole run.
    """
    return min(1.0, max(0.0, float(value)))


def blend_authority(model_authority: float, reputation: float | None) -> float:
    """Fold a remembered reputation into the model's authority judgement."""
    authority = clamp_unit(model_authority)
    if reputation is None:
        return authority
    prior = clamp_unit(reputation)
    return clamp_unit(
        (1.0 - REPUTATION_BLEND) * authority + REPUTATION_BLEND * prior
    )


def overall_score(
    *,
    authority: float,
    recency: float,
    relevance: float,
) -> float:
    """Combine the three source-quality dimensions into one ``UnitScore``."""
    return clamp_unit(
        AUTHORITY_WEIGHT * clamp_unit(authority)
        + RECENCY_WEIGHT * clamp_unit(recency)
        + RELEVANCE_WEIGHT * clamp_unit(relevance)
    )


def build_rationale(
    model_rationale: str,
    *,
    reputation: float | None,
    sub_topics: Sequence[str],
    signals: Sequence[str] = (),
) -> str:
    """Extend the model's rationale with facts this project computed.

    Always returns a non-blank string: ``ScoredSource.rationale`` requires
    one, and a model that returned a blank rationale must not be able to
    fail validation for a whole source. ``signals`` are the read-derived
    identity and dating facts the assessment rested on, so a reviewer can see
    what the judgement was anchored to rather than only what the model said.
    """
    parts: list[str] = []
    text = " ".join(model_rationale.split())
    if text:
        parts.append(summarize_text(text, limit=_RATIONALE_CHARS))
    parts.append(f"Cited for: {', '.join(sub_topics) or 'no sub-topic'}.")
    if signals:
        parts.append(
            "Signals: "
            + summarize_text("; ".join(signals), limit=_RATIONALE_CHARS)
            + "."
        )
    if reputation is None:
        parts.append("No prior reputation on record.")
    else:
        parts.append(
            f"Prior reputation {reputation:.2f} blended into authority."
        )
    return " ".join(parts)


class SourceFitness(NamedTuple):
    """The read-backed identity and fitness fields of one source record."""

    fields: dict[str, object]
    signals: list[str]


def source_fitness(
    group: SourceGroup,
    draft: SourceScoreDraft,
    *,
    dossier: ReadDossier | None,
) -> SourceFitness:
    """Derive the identity, transport, role, and dating fields of one source.

    Every field that is a claim about the world outside the model's head is
    validated against the read the model was shown: the issuer anchor must be
    an attribution the document states, the dates must be years the document
    carries, and a role or a non-original transport relation requires an
    evidenced publisher to be about. Without a read there is nothing to
    validate against, so no identity and no dating is recorded at all — the
    three quality scores are model judgements and are recorded regardless,
    because that is what they are.

    The accepted anchors are recorded as ``identity_anchors`` so the caller
    can resolve this source's work across every read of the run; the identity
    stamped here is that resolution over this one read, and the caller's
    batch resolution replaces it.
    """
    read: ReadRecord | None = dossier.read if dossier is not None else None
    anchors: dict[str, object] = {
        "issuer": draft.issuer,
        "doi": draft.doi,
        "year": draft.year,
        "report_number": draft.report_number,
        "derived_from": list(draft.derived_from),
    }
    accepted: dict[str, object] = {}
    rejected: list[str] = []
    if read is not None:
        accepted = validate_metadata_anchors(read, anchors)
        rejected = rejected_anchor_names(read, anchors)
    issuer_evidenced = "issuer" in accepted
    role = validated_source_role(
        draft.source_role, issuer_evidenced=issuer_evidenced
    )
    transport = validated_transport_relation(
        draft.transport_relation, issuer_evidenced=issuer_evidenced
    )
    self_interest = validated_self_interest(draft.self_interest, role=role)
    temporal = validated_temporal(
        read,
        publication_date=draft.publication_date,
        data_period=draft.data_period,
        forecast_horizon=draft.forecast_horizon,
        effective_date=draft.effective_date,
        status=draft.freshness_status,
    )
    fields: dict[str, object] = {
        "serving_host": read_serving_host(read) if read is not None else None,
        **_read_fitness_identity(read, accepted),
        "transport_relation": transport,
        "source_role": role,
        "self_interest": self_interest,
        "temporal": temporal,
        "cited_sub_topics": list(group.sub_topics),
        "target_ids": list(read.target_ids) if read is not None else [],
        "assessment_revision": (
            dossier.assessment_revision if dossier is not None else ""
        ),
    }
    return SourceFitness(
        fields=fields,
        signals=_fitness_signals(fields, rejected=rejected),
    )


def _read_fitness_identity(
    read: ReadRecord | None, anchors: Mapping[str, object]
) -> dict[str, object]:
    """The identity fields this source's own read establishes, before batching.

    Resolved through the same :func:`resolve_read_identities` the whole
    snapshot is resolved through, over this one read — so a record built on
    its own is never identified by a different rule than the snapshot it
    joins. No read, no identity: every field stays unknown.
    """
    if read is None:
        return {"publisher_id": None, "work_id": None, "identity_anchors": {}}
    ((identity, publisher_id),) = resolve_read_identities(
        [ReadIdentityRequest(read, anchors)]
    )
    return {
        "publisher_id": publisher_id,
        "work_id": identity.key,
        "work_identity": identity,
        "identity_anchors": dict(anchors),
    }


def _fitness_signals(
    fields: Mapping[str, object],
    *,
    rejected: Sequence[str],
) -> list[str]:
    """One reviewable line per read-derived judgement, for the rationale."""
    temporal = fields["temporal"]
    signals = [
        f"role={fields['source_role']}",
        f"transport={fields['transport_relation']}",
        f"self_interest={fields['self_interest']}",
        f"freshness={temporal.status}",  # type: ignore[union-attr]
    ]
    if fields["publisher_id"]:
        signals.append(f"publisher={fields['publisher_id']}")
    if fields["serving_host"]:
        signals.append(f"served_from={fields['serving_host']}")
    if fields["work_id"]:
        signals.append(f"work={fields['work_id']}")
    if rejected:
        signals.append(
            "anchors not evidenced in the read: " + ", ".join(rejected)
        )
    return signals


def build_scored_source(
    group: SourceGroup,
    draft: SourceScoreDraft,
    *,
    reputation: float | None,
    dossier: ReadDossier | None = None,
) -> ScoredSource:
    """Stamp one model score into a validated ``ScoredSource`` record.

    ``overall_score`` remains a pure function of the three quality dimensions.
    Role, transport relation, self-interest, and dates are recorded beside it
    and never blended into it: they constrain what the source may be used for
    downstream, which is not something a higher score can buy.
    """
    authority = blend_authority(draft.authority_score, reputation)
    recency = clamp_unit(draft.recency_score)
    relevance = clamp_unit(draft.relevance_score)
    overall = overall_score(
        authority=authority,
        recency=recency,
        relevance=relevance,
    )
    fitness = source_fitness(group, draft, dossier=dossier)
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        overall_score=overall,
        evaluation_status="scored",
        methods_score=(
            None if draft.methods_score is None else clamp_unit(draft.methods_score)
        ),
        rationale=build_rationale(
            draft.rationale,
            reputation=reputation,
            sub_topics=group.sub_topics,
            signals=fitness.signals,
        ),
        low_confidence=overall < LOW_CONFIDENCE_THRESHOLD,
        **fitness.fields,  # type: ignore[arg-type]
    )


def fallback_scored_source(
    group: SourceGroup,
    *,
    reason: str,
    dossier: ReadDossier | None = None,
) -> ScoredSource:
    """Record a source that could not be scored by the model.

    No quality number is inferred for an operationally unscored source. The
    explicit status lets reports and metrics distinguish a cap, provider
    failure, and missing model row from a genuinely low-quality judgement.
    What survives a failure is the deterministic identity the read itself
    establishes — where it was served from, and which work its complete
    content hash names — so refinement can target the missing assessment
    without re-reading the document.
    """
    explanation = FALLBACK_REASONS.get(reason)
    if explanation is None:
        raise ValueError(f"unknown fallback reason: {reason}")
    status = {
        "model_unavailable": "unscored_provider",
        "not_scored_by_model": "unscored_missing",
        "over_source_cap": "unscored_cap",
    }.get(reason, reason)
    if status not in {
        "unscored_provider",
        "unscored_missing",
        "unscored_cap",
    }:
        raise ValueError(f"unknown fallback reason: {reason}")
    parts = [
        explanation,
        f"Cited for: {', '.join(group.sub_topics) or 'no sub-topic'}.",
    ]
    identity: dict[str, object] = {}
    if dossier is not None:
        read = dossier.read
        identity = {
            "serving_host": dossier.serving_host,
            **_read_fitness_identity(read, {}),
            "cited_sub_topics": list(group.sub_topics),
            "target_ids": list(read.target_ids),
            "assessment_revision": dossier.assessment_revision,
        }
        parts.append(
            f"Serving host: {dossier.serving_host}. Work: "
            f"{identity['work_id'] or 'not established'}."
        )
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=None,
        recency_score=None,
        relevance_score=None,
        overall_score=None,
        rationale=" ".join(parts),
        evaluation_status=status,
        low_confidence=False,
        **identity,  # type: ignore[arg-type]
    )


def average_score(sources: Sequence[ScoredSource]) -> float | None:
    """Mean scored ``overall_score``, or ``None`` when nothing was scored.

    Rounded and zero-guarded because this value lands in
    ``ResearchEvent.metadata``, which rejects non-finite JSON numbers.
    """
    scores = [
        source.overall_score
        for source in sources
        if source.evaluation_status == "scored"
        and source.overall_score is not None
    ]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


def low_confidence_count(sources: Sequence[ScoredSource]) -> int:
    """How many scored sources carry the explicit low-confidence flag."""
    return sum(
        1
        for source in sources
        if source.evaluation_status == "scored" and source.low_confidence
    )


def evaluation_status_counts(
    sources: Sequence[ScoredSource],
) -> dict[str, int]:
    """Return deterministic counts for each source evaluation status."""
    counts = {
        "scored_count": 0,
        "unscored_cap_count": 0,
        "unscored_provider_count": 0,
        "unscored_missing_count": 0,
    }
    for source in sources:
        key = f"{source.evaluation_status}_count"
        if key in counts:
            counts[key] += 1
    return counts


def dossier_group(dossier: ReadDossier) -> SourceGroup:
    """The one-source group a read-backed dossier is scored as.

    The service has reads where the agent has finding groups. Synthesizing the
    group keeps one record builder for both paths, so a read-backed source
    cannot be assembled by different rules than an agent-scored one.
    """
    return SourceGroup(
        url=dossier.url,
        domain=dossier.serving_host,
        title=dossier.read.title,
        sub_topics=list(dossier.cited_sub_topics),
    )


def reused_assessment(
    previous: ScoredSource | None,
    dossier: ReadDossier | None,
) -> bool:
    """True when a stored assessment still describes the source in hand.

    A record with no read behind it can only be keyed by its canonical URL,
    which is the legacy behavior. A read-backed source is keyed by its
    assessment revision, so changed content, metadata, or dating signals
    invalidate the stored judgement instead of surviving it.
    """
    if previous is None or previous.evaluation_status != "scored":
        return False
    if dossier is None:
        return True
    return bool(previous.assessment_revision) and (
        previous.assessment_revision == dossier.assessment_revision
    )


async def assess_new_sources(
    provider: AgentCompleter,
    reads: Sequence[ReadRecord],
    existing: Sequence[ScoredSource] = (),
    *,
    cited_sub_topics: Mapping[str, Sequence[str]] | None = None,
    queries: Mapping[str, str] | None = None,
    reputations: Mapping[str, float] | None = None,
    instruction: str = "Score each source on its fitness for the research.",
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_total_sources: int = DEFAULT_MAX_TOTAL_SOURCES,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    known_reads: Iterable[ReadRecord] = (),
) -> list[ScoredSource]:
    """Assess newly read sources and return the cumulative source snapshot.

    The one assessment service both the Source Evaluator and the Fact Checker
    use, so a document retrieved during verification is scored before it may
    carry a report statement and no graph trip back to the evaluator is
    needed to score it. It is async and tool-free: it calls the provider and
    nothing else, and it performs no I/O.

    Order of work, which is also the order the guarantees are made in:
    validate the read-backed dossiers; reuse the assessments whose content,
    metadata, and dating fingerprint is unchanged; ask the model only about
    what is left; validate every proposed metadata anchor against the read it
    was shown; merge into ``existing`` so the snapshot stays cumulative and
    every earlier source survives; and resolve publisher and work identity
    across the whole snapshot over ``known_reads`` plus ``reads`` — one
    resolution, so a copy read now and its original read earlier are one work.

    A provider or schema failure is never a quality judgement: the affected
    sources keep the identity their read establishes, carry an explicit
    unscored status, and are returned so refinement can target the missing
    assessment.
    """
    if batch_size < 1 or max_total_sources < 1 or excerpt_chars < 1:
        raise ValueError(
            "batch_size, max_total_sources, and excerpt_chars must be at least 1"
        )
    all_reads = [*known_reads, *reads]
    dossiers = build_read_dossiers(
        reads,
        cited_sub_topics=cited_sub_topics,
        queries=queries,
        excerpt_chars=excerpt_chars,
    )
    if not dossiers:
        return resolve_source_identities(
            merge_source_snapshot(existing, []), all_reads
        )

    prior = {normalize_source_url(source.url): source for source in existing}
    seeds = {
        normalize_source_url(url): clamp_unit(score)
        for url, score in (reputations or {}).items()
    }
    reused: dict[str, ScoredSource] = {}
    pending: list[ReadDossier] = []
    for dossier in dossiers:
        previous = prior.get(dossier.url)
        if reused_assessment(previous, dossier):
            reused[dossier.url] = previous  # type: ignore[assignment]
        else:
            pending.append(dossier)

    to_score = pending[:max_total_sources]
    capped = {dossier.url for dossier in pending[max_total_sources:]}
    assessed: dict[str, ScoredSource] = {}
    for start in range(0, len(to_score), batch_size):
        batch = to_score[start : start + batch_size]
        request = SourceEvaluationTask(
            instruction=instruction,
            groups=[dossier_group(dossier) for dossier in batch],
            reputations={
                dossier.url: seeds[dossier.url]
                for dossier in batch
                if dossier.url in seeds
            },
            dossiers={dossier.url: dossier for dossier in batch},
        )
        try:
            response = await provider.complete_structured(
                scoring_messages(request, excerpt_chars=excerpt_chars),
                SourceScoresDraft,
                agent_name=SOURCE_EVALUATOR_NAME,
            )
        except ProviderError:
            for remaining in to_score[start:]:
                assessed[remaining.url] = fallback_scored_source(
                    dossier_group(remaining),
                    reason="unscored_provider",
                    dossier=remaining,
                )
            break

        drafts: dict[str, SourceScoreDraft] = {}
        allowed = {dossier.url for dossier in batch}
        for draft in response.sources:
            url = normalize_source_url(draft.url)
            if url in allowed:
                drafts[url] = draft
        for dossier in batch:
            draft = drafts.get(dossier.url)
            if draft is None:
                assessed[dossier.url] = fallback_scored_source(
                    dossier_group(dossier),
                    reason="unscored_missing",
                    dossier=dossier,
                )
            else:
                assessed[dossier.url] = build_scored_source(
                    dossier_group(dossier),
                    draft,
                    reputation=seeds.get(dossier.url),
                    dossier=dossier,
                )

    records: list[ScoredSource] = []
    for dossier in dossiers:
        if dossier.url in reused:
            records.append(reused[dossier.url])
        elif dossier.url in assessed:
            records.append(assessed[dossier.url])
        elif dossier.url in capped:
            # Past this run's cap: recorded, never dropped, and still carrying
            # the identity its read establishes.
            records.append(
                fallback_scored_source(
                    dossier_group(dossier),
                    reason="unscored_cap",
                    dossier=dossier,
                )
            )
        else:
            # Unreachable for a well-formed dossier list; preserving an
            # explicit state is better than silently dropping the record.
            records.append(
                fallback_scored_source(
                    dossier_group(dossier),
                    reason="unscored_missing",
                    dossier=dossier,
                )
            )
    return resolve_source_identities(
        merge_source_snapshot(existing, records), all_reads
    )


class ReputationSource(Protocol):
    """The one long-term-memory capability this agent needs.

    ``deep_research.memory.long_term.LongTermMemory`` satisfies it
    structurally. Keeping the protocol to a single method keeps test
    doubles small and keeps this agent out of the vector-store's
    construction path.
    """

    async def get_source_reputation(self, url: str) -> SourceReputation | None:
        """Return the stored reputation for one source, if any."""
        raise NotImplementedError


class SourceEvaluationTask(AgentTask):
    """An ``AgentTask`` bound to the sources one evaluation pass scores.

    Carrying the groups on the task is what lets ``finalize(task, run)``
    score without the agent holding mutable state across await points —
    the same reason ``researcher.SubTopicTask`` exists. ``dossiers`` carries
    the read-backed view of any group whose URL this run actually read, keyed
    by canonical URL; a group without one is scored from its finding excerpts
    alone and records no identity.
    """

    groups: list[SourceGroup] = Field(default_factory=list)
    reputations: dict[str, float] = Field(default_factory=dict)
    dossiers: dict[str, ReadDossier] = Field(default_factory=dict)


# How much of one obligation's plan text a passage-selection query may carry.
# The selector is lexical, and a long query dilutes it until the page's opening
# outranks the passage that states the figure.
DOSSIER_QUERY_CHARS = 400


def dossier_queries(
    labels: Sequence[str], sub_topics: Sequence[SubTopic]
) -> list[str]:
    """One passage-selection query per obligation a source was cited for.

    The Source Evaluator's own path scored from a read's first characters in
    document order, so a figure past that window was invisible to the pass that
    judges relevance and identity. The plan is the query that path has: one
    query per cited sub-topic, from its title, the searches it was made with,
    its success criteria, and the question of each evidence target.

    One query *per obligation*, never one merged string: the citations are
    ranked separately and interleaved, so the passage that states the second
    sub-topic's figure is not dropped by the first sub-topic's lexical winner.
    A label the plan does not name is kept as itself, so a record written
    before the plan was labelled still orders its passages.
    """
    by_label: dict[str, str] = {}
    for topic in sub_topics:
        parts = [
            part
            for part in (
                topic.title,
                *topic.search_queries,
                *topic.success_criteria,
                *(target.question for target in topic.evidence_targets),
            )
            if isinstance(part, str) and part.strip()
        ]
        if not parts:
            continue
        text = " ".join(parts)[:DOSSIER_QUERY_CHARS]
        by_label[topic.coverage_id.casefold()] = text
        by_label.setdefault(topic.title.casefold(), text)
    queries: list[str] = []
    for label in labels:
        query = by_label.get(label.casefold(), label)
        if query and query not in queries:
            queries.append(query)
    return queries


def scoring_messages(
    task: SourceEvaluationTask,
    *,
    excerpt_chars: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured scoring draft.

    A group this run read is rendered from the read itself — the host that
    served it, the document's own text, what it was cited for — so the
    judgement is about the document rather than about a finding's paraphrase
    of it. A group with no read keeps the findings-derived dossier.
    """
    dossiers = [
        (
            render_source_dossier(
                group,
                index=index,
                reputation=task.reputations.get(group.url),
                excerpt_chars=excerpt_chars,
            )
            if task.dossiers.get(group.url) is None
            else render_read_dossier(
                task.dossiers[group.url],
                index=index,
                reputation=task.reputations.get(group.url),
                excerpt_chars=excerpt_chars,
            )
        )
        for index, group in enumerate(task.groups, start=1)
    ]
    sections = [f"# Research question\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"# Context\n{task.guidance}")
    sections.append("# Sources\n" + "\n\n".join(dossiers))
    sections.append(f"# Scoring contract\n{SOURCE_SCORING_INSTRUCTION}")
    sections.append(
        "# Reply format\n"
        f"{render_structured_reply_format(_SOURCE_SCORE_REPLY_EXAMPLES)}"
    )
    return [
        ChatMessage(role="developer", content=SOURCE_EVALUATOR_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def reputation_lookup_error(*, failures: int, sources: int) -> ResearchError:
    """Warn that remembered reputations could not be read.

    Recoverable: scoring continues from the dossiers alone, which is the
    spec's "continue with direct scoring" path. Carries counts only —
    never the backend's exception text.
    """
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_reputation_unavailable",
        message=(
            "Remembered source reputations could not be read; sources were "
            "scored directly instead."
        ),
        details={"failures": failures, "sources": sources},
    )


def scoring_provider_error(error: Exception, *, sources: int) -> ResearchError:
    """Record that the scoring call could not reach the provider.

    Non-recoverable, mirroring ``researcher.extraction_provider_error``:
    every affected source still gets an explicit provider-status record, but
    no model judgement exists for this pass.
    """
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_scoring_provider_error",
        message=(
            "The model provider failed while sources were scored; affected "
            "sources were recorded as unscored instead."
        ),
        recoverable=False,
        details=agent_provider_failure_details(
            "source_evaluator_scoring", error, sources=sources
        ),
    )


def no_sources_error() -> ResearchError:
    """Warn that there was nothing to evaluate at all."""
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_no_sources",
        message="No findings were available to evaluate.",
    )


def evaluation_started_event(
    *,
    finding_count: int,
    source_count: int,
) -> ResearchEvent:
    """Announce that evaluation began, before any provider call."""
    return agent_event(
        agent_name=SOURCE_EVALUATOR_NAME,
        event_type="source_evaluator.evaluation.started",
        message="Source evaluation started.",
        metadata={
            "finding_count": finding_count,
            "source_count": source_count,
        },
    )


def evaluation_completed_event(
    sources: Sequence[ScoredSource],
    *,
    reputation_hits: int,
    reputation_failures: int,
) -> ResearchEvent:
    """Report quality and evaluation-status counts for this agent.

    ``average_score`` is rounded and returns ``None`` when no source was
    scored, preserving the distinction between no judgement and a numeric
    quality value.
    """
    status_counts = evaluation_status_counts(sources)
    return agent_event(
        agent_name=SOURCE_EVALUATOR_NAME,
        event_type="source_evaluator.evaluation.completed",
        message="Source evaluation complete.",
        metadata={
            "source_count": len(sources),
            "average_score": average_score(sources),
            "low_confidence_count": low_confidence_count(sources),
            "unique_source_count": len(sources),
            **status_counts,
            "reputation_hits": reputation_hits,
            "reputation_failures": reputation_failures,
        },
    )


class SourceEvaluatorAgent(BaseAgent[EvaluatedSources]):
    """Score every source behind ``state.raw_findings``.

    Runs no ReAct loop: grouping is deterministic, and the reputation read is
    an exact-id memory lookup rather than a search.
    ``run`` is overridden for the same reason ``ResearcherAgent`` overrides
    it — the shared single-loop ``BaseAgent.run`` cannot express this
    agent's shape.
    """

    name = SOURCE_EVALUATOR_NAME
    description = "Score the sources behind the collected findings."
    allowed_tools: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        provider: AgentCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        model_profile: EffectiveModelConfig | None = None,
        reputation: ReputationSource | None = None,
        batch_size: int | None = None,
        max_total_sources: int | None = None,
        max_sources: int | None = None,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    ) -> None:
        runtime_config = config or AgentRuntimeConfig()
        evaluator_config = runtime_config.source_evaluator
        if max_sources is not None:
            if (
                max_total_sources is not None
                and max_total_sources != max_sources
            ):
                raise ValueError(
                    "max_sources and max_total_sources must agree"
                )
            max_total_sources = max_sources
        resolved_batch_size = (
            evaluator_config.batch_size
            if batch_size is None
            else batch_size
        )
        resolved_max_total_sources = (
            evaluator_config.max_total_sources
            if max_total_sources is None
            else max_total_sources
        )
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=runtime_config,
            model_profile=model_profile,
        )
        if resolved_batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if resolved_max_total_sources < 1:
            raise ValueError("max_total_sources must be at least 1")
        if excerpt_chars < 1:
            raise ValueError("excerpt_chars must be at least 1")
        self._reputation = reputation
        self._batch_size = resolved_batch_size
        self._max_total_sources = resolved_max_total_sources
        self._excerpt_chars = excerpt_chars
        # The canonical snapshot of the state this run was handed, and the
        # run's reads, captured by ``run`` so ``state_update`` can merge into
        # the one and resolve identity over the other. Empty until a run
        # starts, which keeps a directly-invoked ``state_update`` total.
        self._prior_sources: list[ScoredSource] = []
        self._reads: list[ReadRecord] = []

    @property
    def output_schema(self) -> type[EvaluatedSources]:
        """The validated scores. Never sent to the provider.

        ``score_sources`` asks for ``SourceScoresDraft`` instead, because
        ``ScoredSource`` carries ``Field`` and ``UnitScore`` constraints
        that do not survive strict JSON schema conversion. Do not route
        this agent through ``complete_output``.
        """
        return EvaluatedSources

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return SOURCE_EVALUATOR_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> SourceEvaluationTask:
        """Group findings once, seed reputations, and attach the reads.

        A group whose canonical URL this run actually read is scored from the
        read — its title, its serving host, its own text — so its record can
        carry a publisher, a work, and an assessment revision instead of only
        a URL a model reported. A group with no read keeps the findings-derived
        dossier and records no identity.
        """
        groups = group_findings_by_url(state.raw_findings)
        seeded = {
            normalize_source_url(url): float(score)
            for url, score in state.memory_context.known_source_reputations.items()
        }
        reputations = {
            group.url: seeded[group.url]
            for group in groups
            if group.url in seeded
        }
        dossiers = {
            dossier.url: dossier
            for dossier in build_read_dossiers(
                list(state.read_records.values()),
                cited_sub_topics={
                    group.url: group.sub_topics for group in groups
                },
                queries={
                    group.url: [
                        # What this run actually extracted from the source, then
                        # the plan text of every obligation it was cited for.
                        *(finding.content[:DOSSIER_QUERY_CHARS] for finding in group.findings),
                        *dossier_queries(group.sub_topics, state.sub_topics),
                    ]
                    for group in groups
                },
                excerpt_chars=self._excerpt_chars,
            )
        }
        return SourceEvaluationTask(
            instruction=state.original_question,
            groups=groups,
            reputations=reputations,
            dossiers=dossiers,
        )

    async def lookup_reputations(
        self,
        task: SourceEvaluationTask,
    ) -> tuple[SourceEvaluationTask, list[ResearchError], int]:
        """Refresh remembered reputations, tolerating a failing backend.

        A live lookup wins over the ``memory_context`` seed. Any failure
        leaves the seed in place, records one recoverable error for the
        whole pass, and lets scoring continue — the spec's "continue with
        direct scoring" requirement.
        """
        if self._reputation is None or not task.groups:
            return task, [], 0

        reputations = dict(task.reputations)
        failures = 0
        hits = 0
        for group in task.groups:
            try:
                record = await self._reputation.get_source_reputation(group.url)
            except Exception:
                # Deliberately broad: a memory backend can raise anything,
                # and no backend failure is worth failing the pass over.
                failures += 1
                continue
            if record is not None:
                reputations[group.url] = clamp_unit(record.reputation_score)
                hits += 1
        errors = (
            [reputation_lookup_error(failures=failures, sources=len(task.groups))]
            if failures
            else []
        )
        return (
            task.model_copy(update={"reputations": reputations}),
            errors,
            hits,
        )

    async def score_sources(
        self,
        task: SourceEvaluationTask,
    ) -> tuple[list[ScoredSource], list[ResearchError], bool]:
        """Score canonical sources in deterministic batches.

        Previously scored sources are reused, so refinement only spends
        provider calls on new or unscored URLs — or on a source whose
        read-backed assessment revision changed, which is what keeps a
        revised document from being credited with its earlier score. A
        provider failure stops the current batch and marks that batch plus
        later unscored URLs with an explicit status; successful earlier
        batches remain intact.
        """
        if not task.groups:
            return [], [], False

        prior = {
            normalize_source_url(source.url): source
            for source in self._prior_sources
        }
        # The total cap applies to sources still needing an assessment. A
        # prior scored snapshot is complete evidence and must not consume the
        # current pass's budget; unscored records remain eligible for retry.
        eligible_groups: list[SourceGroup] = []
        for group in task.groups:
            if not reused_assessment(
                prior.get(group.url), task.dossiers.get(group.url)
            ):
                eligible_groups.append(group)
        groups_to_score = eligible_groups[: self._max_total_sources]
        capped = {
            group.url
            for group in eligible_groups[self._max_total_sources :]
        }

        errors: list[ResearchError] = []
        provider_failed = False
        assessed: dict[str, ScoredSource] = {}
        for start in range(0, len(groups_to_score), self._batch_size):
            batch = groups_to_score[start : start + self._batch_size]
            request = task.model_copy(update={"groups": batch})
            try:
                response = await self.provider.complete_structured(
                    scoring_messages(request, excerpt_chars=self._excerpt_chars),
                    SourceScoresDraft,
                    agent_name=self.name,
                )
            except ProviderError as error:
                provider_failed = True
                errors.append(scoring_provider_error(error, sources=len(batch)))
                for remaining in groups_to_score[start:]:
                    assessed[remaining.url] = fallback_scored_source(
                        remaining,
                        reason="unscored_provider",
                        dossier=task.dossiers.get(remaining.url),
                    )
                break

            drafts: dict[str, SourceScoreDraft] = {}
            allowed_urls = {group.url for group in batch}
            for draft in response.sources:
                url = normalize_source_url(draft.url)
                if url in allowed_urls:
                    # Last model row wins within this response, matching the
                    # canonical snapshot's latest-record-wins contract.
                    drafts[url] = draft
            for group in batch:
                draft = drafts.get(group.url)
                if draft is None:
                    assessed[group.url] = fallback_scored_source(
                        group,
                        reason="unscored_missing",
                        dossier=task.dossiers.get(group.url),
                    )
                else:
                    assessed[group.url] = build_scored_source(
                        group,
                        draft,
                        reputation=task.reputations.get(group.url),
                        dossier=task.dossiers.get(group.url),
                    )

        sources: list[ScoredSource] = []
        for group in task.groups:
            previous = prior.get(group.url)
            if reused_assessment(previous, task.dossiers.get(group.url)):
                sources.append(previous)  # type: ignore[arg-type]
            elif group.url in assessed:
                sources.append(assessed[group.url])
            elif group.url in capped:
                sources.append(
                    fallback_scored_source(
                        group,
                        reason="unscored_cap",
                        dossier=task.dossiers.get(group.url),
                    )
                )
            else:
                # This branch is reachable only for a malformed task with a
                # duplicate canonical URL; preserve an explicit state rather
                # than silently dropping the record.
                sources.append(
                    fallback_scored_source(
                        group,
                        reason="unscored_missing",
                        dossier=task.dossiers.get(group.url),
                    )
                )
        return sources, errors, provider_failed

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> EvaluatedSources | None:
        """Adapt ``score_sources`` to the ``BaseAgent`` hook.

        ``run`` calls ``score_sources`` directly so it can keep the errors
        this hook signature has nowhere to return.
        """
        del run
        if not isinstance(task, SourceEvaluationTask):
            raise AgentConfigurationError(
                "SourceEvaluatorAgent.finalize requires a SourceEvaluationTask"
            )
        sources, _, _ = await self.score_sources(task)
        return EvaluatedSources(sources=sources)

    def state_update(
        self,
        result: EvaluatedSources | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The complete source snapshot and errors. ``run`` adds the events.

        ``evaluated_sources`` replaces rather than appends, so this update
        carries every source assessed so far — the ones found on the state
        this run was handed, merged with the ones it just scored. Without
        that merge the second pass would silently erase the first pass's
        sources.
        """
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["evaluated_sources"] = self._snapshot(result.sources)
        return update

    def _snapshot(self, sources: Sequence[ScoredSource]) -> list[ScoredSource]:
        """The cumulative snapshot, identified across every read of the run.

        One resolution over the whole snapshot, not one per source: a mirror
        scored now and the original it copies scored in an earlier pass are
        one work only when both are resolved together.
        """
        return resolve_source_identities(
            merge_source_snapshot(self._prior_sources, sources), self._reads
        )

    async def run(self, state: ResearchState) -> AgentRun[EvaluatedSources]:
        """Group, look up reputations, score, and report the counts.

        No ReAct loop runs, so the returned ``ReActRun`` is a synthetic
        record with zero iterations and zero tool calls. ``stop_reason`` is
        ``"provider_error"`` only when the scoring call itself failed, so a
        caller reading ``react.succeeded`` learns the same thing it would
        from any other agent.
        """
        task = self.build_task(state)
        self._prior_sources = list(state.evaluated_sources)
        self._reads = list(state.read_records.values())
        events: list[ResearchEvent] = [
            evaluation_started_event(
                finding_count=len(state.raw_findings),
                source_count=len(task.groups),
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            task, lookup_errors, hits = await self.lookup_reputations(task)
            errors.extend(lookup_errors)
            sources, scoring_errors, provider_failed = await self.score_sources(
                task
            )
            errors.extend(scoring_errors)
            if not task.groups:
                errors.append(no_sources_error())
            failures = sum(
                int(error.details.get("failures", 0) or 0)
                for error in lookup_errors
            )
            snapshot = self._snapshot(sources)
            events.append(
                evaluation_completed_event(
                    snapshot,
                    reputation_hits=hits,
                    reputation_failures=failures,
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "source_count": len(snapshot),
                    "average_score": average_score(snapshot),
                    "low_confidence_count": low_confidence_count(snapshot),
                    "unique_source_count": len(snapshot),
                    **evaluation_status_counts(snapshot),
                    "reputation_hits": hits,
                    "reputation_failures": failures,
                }
            )

        react = ReActRun(
            agent_name=self.name,
            stop_reason="provider_error" if provider_failed else "finished",
            errors=errors,
        )
        result = EvaluatedSources(sources=snapshot)
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(result, react),
                "events": events,
            },
            call_fingerprints=dict(self._call_fingerprints),
        )
