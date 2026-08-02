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
from typing import Protocol

from pydantic import Field

from deep_research.agents.base import BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.prompts import (
    SOURCE_EVALUATOR_SYSTEM_PROMPT,
    SOURCE_SCORING_INSTRUCTION,
    AgentTask,
    render_source_dossier,
)
from deep_research.agents.sources import (
    SourceGroup,
    corroboration_score,
    group_findings_by_url,
    normalize_source_url,
)
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.memory.entries import SourceReputation
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    ContractModel,
    ResearchError,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
)

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
    the same reason ``researcher.SubTopicTask`` exists.
    """

    groups: list[SourceGroup] = Field(default_factory=list)
    corroborations: dict[str, float] = Field(default_factory=dict)
    reputations: dict[str, float] = Field(default_factory=dict)


def scoring_messages(
    task: SourceEvaluationTask,
    *,
    excerpt_chars: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured scoring draft."""
    dossiers = [
        render_source_dossier(
            group,
            index=index,
            corroboration=task.corroborations.get(group.url, 0.0),
            reputation=task.reputations.get(group.url),
            excerpt_chars=excerpt_chars,
        )
        for index, group in enumerate(task.groups, start=1)
    ]
    sections = [f"## Research question\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Context\n{task.guidance}")
    sections.append("## Sources\n" + "\n\n".join(dossiers))
    sections.append(f"## Scoring contract\n{SOURCE_SCORING_INSTRUCTION}")
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
    every source still gets a low-confidence fallback record, but no
    model judgement exists for this pass.
    """
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_scoring_provider_error",
        message=(
            "The model provider failed while sources were scored; every "
            "source was recorded as low confidence instead."
        ),
        recoverable=False,
        details={
            "exception_type": type(error).__name__,
            "sources": sources,
        },
    )


def no_sources_error() -> ResearchError:
    """Warn that there was nothing to evaluate at all."""
    return agent_error(
        agent_name=SOURCE_EVALUATOR_NAME,
        error_type="source_evaluator_no_sources",
        message="No findings were available to evaluate.",
    )


class SourceEvaluatorAgent(BaseAgent[EvaluatedSources]):
    """Score every source behind ``state.raw_findings``.

    Runs no ReAct loop: grouping and corroboration are deterministic, and
    the reputation read is an exact-id memory lookup rather than a search.
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
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        reputation: ReputationSource | None = None,
        max_sources: int = DEFAULT_MAX_SOURCES,
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if max_sources < 1:
            raise ValueError("max_sources must be at least 1")
        if excerpt_chars < 1:
            raise ValueError("excerpt_chars must be at least 1")
        self._reputation = reputation
        self._max_sources = max_sources
        self._excerpt_chars = excerpt_chars

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
        """Group findings, compute corroboration, seed remembered scores."""
        groups = group_findings_by_url(state.raw_findings)
        corroborations = {
            group.url: corroboration_score(group, groups) for group in groups
        }
        seeded = {
            normalize_source_url(url): float(score)
            for url, score in state.memory_context.known_source_reputations.items()
        }
        reputations = {
            group.url: seeded[group.url]
            for group in groups
            if group.url in seeded
        }
        return SourceEvaluationTask(
            instruction=state.original_question,
            groups=groups,
            corroborations=corroborations,
            reputations=reputations,
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
        """Score every grouped source, in ``task.groups`` order.

        The third element, ``provider_failed``, is ``True`` only when the
        scoring call itself could not reach the provider. Even then every
        source gets a record: the acceptance criterion is "*every* source
        used by findings", and a silent gap is worse than a flagged
        low-confidence row.
        """
        if not task.groups:
            return [], [], False

        scored_groups = task.groups[: self._max_sources]
        capped_groups = task.groups[self._max_sources :]
        capped = {group.url for group in capped_groups}
        request = task.model_copy(update={"groups": scored_groups})

        drafts: dict[str, SourceScoreDraft] = {}
        errors: list[ResearchError] = []
        provider_failed = False
        try:
            response = await self.provider.complete_structured(
                scoring_messages(request, excerpt_chars=self._excerpt_chars),
                SourceScoresDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            provider_failed = True
            errors.append(
                scoring_provider_error(error, sources=len(scored_groups))
            )
        else:
            for draft in response.sources:
                url = normalize_source_url(draft.url)
                # First score for a URL wins; a model that repeats itself
                # must not be able to overwrite its own earlier judgement.
                drafts.setdefault(url, draft)

        sources: list[ScoredSource] = []
        for group in task.groups:
            corroboration = task.corroborations.get(group.url, 0.0)
            reputation = task.reputations.get(group.url)
            draft = drafts.get(group.url)
            if draft is not None:
                sources.append(
                    build_scored_source(
                        group,
                        draft,
                        corroboration=corroboration,
                        reputation=reputation,
                    )
                )
                continue
            if group.url in capped:
                reason = "over_source_cap"
            elif provider_failed:
                reason = "model_unavailable"
            else:
                reason = "not_scored_by_model"
            sources.append(
                fallback_scored_source(
                    group,
                    corroboration=corroboration,
                    reputation=reputation,
                    reason=reason,
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
        """Scored sources and errors only. ``run`` adds the events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["evaluated_sources"] = list(result.sources)
        return update
