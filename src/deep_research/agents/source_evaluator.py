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

from collections.abc import Sequence
from typing import Protocol

from pydantic import Field

from deep_research.agents.base import AgentCompleter, AgentRun, BaseAgent
from deep_research.agents.errors import (
    AgentConfigurationError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.agents.events import agent_event
from deep_research.agents.identity import merge_source_snapshot
from deep_research.agents.prompts import (
    SOURCE_EVALUATOR_SYSTEM_PROMPT,
    SOURCE_SCORING_INSTRUCTION,
    AgentTask,
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
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    ContractModel,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
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
DEFAULT_EXCERPT_CHARS = 400
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
    """

    url: str
    authority_score: float
    recency_score: float
    relevance_score: float
    rationale: str


class SourceScoresDraft(ContractModel):
    """The provider-facing scoring schema for one evaluation pass."""

    sources: list[SourceScoreDraft]


# Two examples, because scoring chooses a value on a continuous scale: the
# weak and strong cases are the opposite ends of the same 0.0-1.0 direction,
# and each is internally consistent with its own synthetic dossier.
_SOURCE_SCORE_REPLY_EXAMPLES = (
    (
        "Weak example input: an anonymous, undated post at "
        "https://weak.example.test/post only mentions the topic.",
        '{"sources":[{"url":"https://weak.example.test/post",'
        '"authority_score":0.1,"recency_score":0.5,"relevance_score":0.2,'
        '"rationale":"The publisher is unidentified, there is no dating '
        'signal, and the excerpt only mentions the topic."}]}',
    ),
    (
        "Strong example input: a current primary standard at "
        "https://strong.example.test/standard directly answers the topic.",
        '{"sources":[{"url":"https://strong.example.test/standard",'
        '"authority_score":0.95,"recency_score":0.9,"relevance_score":0.95,'
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
) -> str:
    """Extend the model's rationale with facts this project computed.

    Always returns a non-blank string: ``ScoredSource.rationale`` requires
    one, and a model that returned a blank rationale must not be able to
    fail validation for a whole source.
    """
    parts: list[str] = []
    text = " ".join(model_rationale.split())
    if text:
        parts.append(summarize_text(text, limit=_RATIONALE_CHARS))
    parts.append(f"Cited for: {', '.join(sub_topics) or 'no sub-topic'}.")
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
    reputation: float | None,
) -> ScoredSource:
    """Stamp one model score into a validated ``ScoredSource`` record."""
    authority = blend_authority(draft.authority_score, reputation)
    recency = clamp_unit(draft.recency_score)
    relevance = clamp_unit(draft.relevance_score)
    overall = overall_score(
        authority=authority,
        recency=recency,
        relevance=relevance,
    )
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=authority,
        recency_score=recency,
        relevance_score=relevance,
        overall_score=overall,
        evaluation_status="scored",
        rationale=build_rationale(
            draft.rationale,
            reputation=reputation,
            sub_topics=group.sub_topics,
        ),
        low_confidence=overall < LOW_CONFIDENCE_THRESHOLD,
    )


def fallback_scored_source(
    group: SourceGroup,
    *,
    reason: str,
) -> ScoredSource:
    """Record a source that could not be scored by the model.

    No quality number is inferred for an operationally unscored source. The
    explicit status lets reports and metrics distinguish a cap, provider
    failure, and missing model row from a genuinely low-quality judgement.
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
    rationale = " ".join(
        (
            explanation,
            f"Cited for: {', '.join(group.sub_topics) or 'no sub-topic'}.",
        )
    )
    return ScoredSource(
        url=group.url,
        title=group.title,
        authority_score=None,
        recency_score=None,
        relevance_score=None,
        overall_score=None,
        rationale=rationale,
        evaluation_status=status,
        low_confidence=False,
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
            reputation=task.reputations.get(group.url),
            excerpt_chars=excerpt_chars,
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
        # The canonical snapshot of the state this run was handed, captured by
        # ``run`` so ``state_update`` can merge into it. Empty until a run
        # starts, which keeps a directly-invoked ``state_update`` total.
        self._prior_sources: list[ScoredSource] = []

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
        """Group findings once and seed remembered reputations."""
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
        return SourceEvaluationTask(
            instruction=state.original_question,
            groups=groups,
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
        """Score canonical sources in deterministic batches.

        Previously scored sources are reused, so refinement only spends
        provider calls on new or unscored URLs. A provider failure stops the
        current batch and marks that batch plus later unscored URLs with an
        explicit status; successful earlier batches remain intact.
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
            previous = prior.get(group.url)
            if previous is None or previous.evaluation_status != "scored":
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
                    )
                else:
                    assessed[group.url] = build_scored_source(
                        group,
                        draft,
                        reputation=task.reputations.get(group.url),
                    )

        sources: list[ScoredSource] = []
        for group in task.groups:
            previous = prior.get(group.url)
            if previous is not None and previous.evaluation_status == "scored":
                sources.append(previous)
            elif group.url in assessed:
                sources.append(assessed[group.url])
            elif group.url in capped:
                sources.append(
                    fallback_scored_source(group, reason="unscored_cap")
                )
            else:
                # This branch is reachable only for a malformed task with a
                # duplicate canonical URL; preserve an explicit state rather
                # than silently dropping the record.
                sources.append(
                    fallback_scored_source(group, reason="unscored_missing")
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
            update["evaluated_sources"] = merge_source_snapshot(
                self._prior_sources, result.sources
            )
        return update

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
            snapshot = merge_source_snapshot(self._prior_sources, sources)
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
        result = EvaluatedSources(
            sources=merge_source_snapshot(self._prior_sources, sources)
        )
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(result, react),
                "events": events,
            },
        )
