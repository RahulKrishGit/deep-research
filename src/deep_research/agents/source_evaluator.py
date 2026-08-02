"""The Source Evaluator: score every source the findings actually used.

Like the Planner and the Researcher, this module never sends a domain type
to the provider. ``SourceScoreDraft`` mirrors the model-judged part of
``ScoredSource`` with plain field types so it survives strict JSON schema
conversion, and this module stamps the parts the model must not be trusted
to supply: the canonical URL, the computed corroboration score, the
weighted overall score, and the low-confidence flag.

Score convention: every score is a ``UnitScore`` in ``[0.0, 1.0]``, higher
is better, matching ``Finding.confidence`` and
``SourceReputation.reputation_score``. ``overall_score`` is a convex
combination of the four recorded dimensions, so a ``ScoredSource`` record
can always be re-checked against its own fields.
"""

from __future__ import annotations

from collections.abc import Sequence

from deep_research.agents.sources import SourceGroup
from deep_research.agents.steps import summarize_text
from deep_research.utils.types import ContractModel, ScoredSource

SOURCE_EVALUATOR_NAME = "source_evaluator"

# Weights form a convex combination: overall_score is in [0, 1] whenever
# its four inputs are. Authority and relevance dominate because a source
# that is neither authoritative nor on-topic is not rescued by being new.
AUTHORITY_WEIGHT = 0.35
RECENCY_WEIGHT = 0.15
RELEVANCE_WEIGHT = 0.30
CORROBORATION_WEIGHT = 0.20

# How much a reputation recalled from long-term memory moves the model's
# authority judgement. Blended into authority rather than into the overall
# score so that overall_score stays a pure function of the four recorded
# dimensions.
REPUTATION_BLEND = 0.4

LOW_CONFIDENCE_THRESHOLD = 0.4
DEFAULT_MAX_SOURCES = 12
DEFAULT_EXCERPT_CHARS = 400
_RATIONALE_CHARS = 400

# Enumerated, project-generated reasons a source was recorded without a
# model judgement. Never provider text: these strings reach prompts,
# ResearchError.details, and user-facing rationales.
FALLBACK_REASONS = {
    "model_unavailable": "The scoring model could not be reached.",
    "not_scored_by_model": (
        "The scoring model returned no score for this source."
    ),
    "over_source_cap": "This source fell past this run's scoring cap.",
}


class SourceScoreDraft(ContractModel):
    """One model-judged source score, before domain validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema. ``corroboration_score``, ``overall_score``, and
    ``low_confidence`` are deliberately absent — this project computes
    those, not the model.
    """

    url: str
    authority_score: float
    recency_score: float
    relevance_score: float
    rationale: str


class SourceScoresDraft(ContractModel):
    """The provider-facing scoring schema for one evaluation pass."""

    sources: list[SourceScoreDraft]


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
    corroboration: float,
) -> float:
    """Combine the four recorded dimensions into one ``UnitScore``."""
    return clamp_unit(
        AUTHORITY_WEIGHT * clamp_unit(authority)
        + RECENCY_WEIGHT * clamp_unit(recency)
        + RELEVANCE_WEIGHT * clamp_unit(relevance)
        + CORROBORATION_WEIGHT * clamp_unit(corroboration)
    )


def build_rationale(
    model_rationale: str,
    *,
    corroboration: float,
    reputation: float | None,
    sub_topics: Sequence[str],
) -> str:
    """Extend the model's rationale with the facts this project computed.

    Always returns a non-blank string: ``ScoredSource.rationale`` requires
    one, and a model that returned a blank rationale must not be able to
    fail validation for a whole source.
    """
    parts: list[str] = []
    text = " ".join(model_rationale.split())
    if text:
        parts.append(summarize_text(text, limit=_RATIONALE_CHARS))
    parts.append(f"Cited for: {', '.join(sub_topics) or 'no sub-topic'}.")
    parts.append(
        f"Corroboration {corroboration:.2f} across independent domains."
    )
    if reputation is None:
        parts.append("No prior reputation on record.")
    else:
        parts.append(
            f"Prior reputation {reputation:.2f} blended into authority."
        )
    return " ".join(parts)


def build_scored_source(
    group: SourceGroup,
    draft: SourceScoreDraft,
    *,
    corroboration: float,
    reputation: float | None,
) -> ScoredSource:
    """Stamp one model score into a validated ``ScoredSource`` record."""
    authority = blend_authority(draft.authority_score, reputation)
    recency = clamp_unit(draft.recency_score)
    relevance = clamp_unit(draft.relevance_score)
    corroboration = clamp_unit(corroboration)
    overall = overall_score(
        authority=authority,
        recency=recency,
        relevance=relevance,
        corroboration=corroboration,
    )
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        corroboration_score=corroboration,
        overall_score=overall,
        rationale=build_rationale(
            draft.rationale,
            corroboration=corroboration,
            reputation=reputation,
            sub_topics=group.sub_topics,
        ),
        low_confidence=overall < LOW_CONFIDENCE_THRESHOLD,
    )


def fallback_scored_source(
    group: SourceGroup,
    *,
    corroboration: float,
    reputation: float | None,
    reason: str,
) -> ScoredSource:
    """Record a source that could not be scored by the model.

    The three model-judged dimensions floor at 0.0 rather than being
    guessed at, corroboration is kept because it was computed locally, and
    ``low_confidence`` is always ``True``. This is what makes "every source
    used by findings gets a score or an explicit low-confidence flag" hold
    even when the provider is down.
    """
    explanation = FALLBACK_REASONS.get(reason)
    if explanation is None:
        raise ValueError(f"unknown fallback reason: {reason}")
    authority = blend_authority(0.0, reputation)
    corroboration = clamp_unit(corroboration)
    overall = overall_score(
        authority=authority,
        recency=0.0,
        relevance=0.0,
        corroboration=corroboration,
    )
    rationale = build_rationale(
        explanation,
        corroboration=corroboration,
        reputation=reputation,
        sub_topics=group.sub_topics,
    )
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=authority,
        recency_score=0.0,
        relevance_score=0.0,
        corroboration_score=corroboration,
        overall_score=overall,
        rationale=rationale,
        low_confidence=True,
    )


def average_score(sources: Sequence[ScoredSource]) -> float:
    """Mean ``overall_score``, rounded, and ``0.0`` for an empty run.

    Rounded and zero-guarded because this value lands in
    ``ResearchEvent.metadata``, which rejects non-finite JSON numbers.
    """
    if not sources:
        return 0.0
    total = sum(source.overall_score for source in sources)
    return round(total / len(sources), 4)


def low_confidence_count(sources: Sequence[ScoredSource]) -> int:
    """How many scored sources carry the explicit low-confidence flag."""
    return sum(1 for source in sources if source.low_confidence)
