"""The Critic: score the report and recommend whether research continues.

The provider is asked for ``CritiqueDraft`` and never for ``Critique``:
``Critique`` declares ``CriticScore`` (1..10) and a non-blank rationale,
which strict structured outputs reject. Local code stamps the parts the
model must not be trusted with — the clamped score, the de-duplicated
notes, and above all the routing decision.

Routing convention: ``route_decision`` checks the iteration bound *first*.
"The critic must not continue forever" is the one rule no model judgement
may override, so it is settled before anything the model said is read.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.prompts import (
    CRITIC_SYSTEM_PROMPT,
    CRITIQUE_INSTRUCTION,
    AgentTask,
    render_claim_digest,
    render_react_messages,
    render_source_quality,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.researcher import render_evidence
from deep_research.agents.steps import ReActDecision, ReActRun, ReActStep
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    Claim,
    ContractModel,
    Critique,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    ScoredSource,
)

CRITIC_NAME = "critic"

# The spec's acceptance threshold: a report scoring below this always buys
# another research pass while budget remains.
ACCEPTANCE_SCORE = 7
MIN_CRITIC_SCORE = 1
MAX_CRITIC_SCORE = 10

CRITIC_REPORT_CHARS = 6000
CRITIC_CLAIM_DIGEST = 40
CRITIC_EVIDENCE_CHARS = 2000
DEFAULT_MAX_NOTES = 10

_RATIONALE_CHARS = 600

# Enumerated, project-generated routing reasons. Never provider text: these
# reach ResearchEvent.metadata and the recorded rationale.
ROUTING_REASONS = {
    "accepted_quality": "The report met the acceptance threshold.",
    "max_iterations_reached": (
        "The refinement budget is exhausted; this is the final report."
    ),
    "low_score": "The report scored below the acceptance threshold.",
    "critical_gaps": "Material gaps remain in the research.",
    "unsupported_claims": (
        "The report leans on statements no source supports."
    ),
    "missing_report": "No report was available to review.",
    "provider_unavailable": (
        "The model provider failed while the report was reviewed."
    ),
}

# The two conditions under which no model review exists at all.
CRITIQUE_FALLBACK_REASONS = ("missing_report", "provider_unavailable")


class CritiqueDraft(ContractModel):
    """One model review, before domain validation.

    ``score`` is a plain ``int`` rather than ``CriticScore``: a model that
    answers 0 or 42 is making a formatting mistake, which ``clamp_score``
    fixes locally rather than discarding the whole review over.
    """

    score: int
    gaps: list[str]
    unsupported_claims: list[str]
    recommended_queries: list[str]
    rationale: str


class CritiqueTask(AgentTask):
    """An ``AgentTask`` bound to the report and budget it reviews.

    Carrying the report and the iteration bounds on the task is what lets
    ``finalize(task, run)`` route without the agent holding mutable state
    across await points — the same reason ``ClaimTask`` exists.
    """

    report: str = ""
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=1, ge=1)
    claims: list[Claim] = []
    sources: list[ScoredSource] = []
    sub_topics: list[str] = []
    error_count: int = Field(default=0, ge=0)


def clamp_score(value: int) -> int:
    """Pin a model score into the ``CriticScore`` range."""
    return min(MAX_CRITIC_SCORE, max(MIN_CRITIC_SCORE, int(value)))


def normalize_notes(
    values: Sequence[str],
    *,
    limit: int = DEFAULT_MAX_NOTES,
) -> list[str]:
    """Collapse, de-duplicate, and cap one model-supplied note list."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    notes: list[str] = []
    for value in values:
        note = " ".join(value.split())
        if note and note not in notes:
            notes.append(note)
    return notes[:limit]


def route_decision(
    *,
    score: int,
    gaps: Sequence[str],
    unsupported_claims: Sequence[str],
    iteration: int,
    max_iterations: int,
    has_report: bool,
) -> tuple[bool, str]:
    """Decide routing locally, in a fixed precedence.

    The iteration bound comes first and beats every quality signal. After
    that: a missing report is the most concrete thing to fix, then the
    score threshold, then gaps, then unsupported claims. Every gap the
    model listed counts as critical — ``CRITIQUE_INSTRUCTION`` tells it to
    list a gap only when closing it would materially change the answer.
    """
    if iteration >= max_iterations:
        return False, "max_iterations_reached"
    if not has_report:
        return True, "missing_report"
    if score < ACCEPTANCE_SCORE:
        return True, "low_score"
    if gaps:
        return True, "critical_gaps"
    if unsupported_claims:
        return True, "unsupported_claims"
    return False, "accepted_quality"


def _rationale(model_rationale: str, *, reason: str) -> str:
    """Extend the model's rationale with the routing reason this run used."""
    text = " ".join(model_rationale.split())
    explanation = ROUTING_REASONS[reason]
    body = f"{text} {explanation}" if text else explanation
    return body[:_RATIONALE_CHARS].rstrip()


def build_critique(
    draft: CritiqueDraft,
    *,
    iteration: int,
    max_iterations: int,
) -> tuple[Critique, str]:
    """Stamp one model review into a validated ``Critique`` and its route."""
    score = clamp_score(draft.score)
    gaps = normalize_notes(draft.gaps)
    unsupported = normalize_notes(draft.unsupported_claims)
    queries = normalize_notes(draft.recommended_queries)
    should_continue, reason = route_decision(
        score=score,
        gaps=gaps,
        unsupported_claims=unsupported,
        iteration=iteration,
        max_iterations=max_iterations,
        has_report=True,
    )
    return (
        Critique(
            score=score,
            gaps=gaps,
            unsupported_claims=unsupported,
            recommended_queries=queries,
            should_continue=should_continue,
            rationale=_rationale(draft.rationale, reason=reason),
        ),
        reason,
    )


def fallback_critique(
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> tuple[Critique, str]:
    """Record a review that could not be made, with no invented score.

    A provider outage never buys another research cycle: an outage says
    nothing about the report, and a retry would almost certainly repeat it
    at cost. A missing report does buy one — there is something concrete to
    fix — unless the iteration bound already forbids it.
    """
    if reason not in CRITIQUE_FALLBACK_REASONS:
        raise ValueError(f"unknown fallback reason: {reason}")
    if reason == "provider_unavailable":
        should_continue = False
        route = (
            "max_iterations_reached"
            if iteration >= max_iterations
            else reason
        )
        gaps: list[str] = []
    else:
        should_continue, route = route_decision(
            score=MIN_CRITIC_SCORE,
            gaps=[],
            unsupported_claims=[],
            iteration=iteration,
            max_iterations=max_iterations,
            has_report=False,
        )
        gaps = ["No report was available to review."]
    sentences = [ROUTING_REASONS[reason]]
    if route != reason:
        sentences.append(ROUTING_REASONS[route])
    return (
        Critique(
            score=MIN_CRITIC_SCORE,
            gaps=gaps,
            unsupported_claims=[],
            recommended_queries=[],
            should_continue=should_continue,
            rationale=" ".join(sentences),
        ),
        route,
    )


def _clamp_report(text: str, *, limit: int) -> str:
    """Clamp the report for a prompt without flattening its headings.

    ``summarize_text`` is deliberately not used here: it joins on
    whitespace, which would run every Markdown heading into one line.
    """
    report = text.strip()
    if not report:
        return "(no report)"
    if len(report) <= limit:
        return report
    return report[: limit - 3].rstrip() + "..."


def critique_messages(
    task: CritiqueTask,
    run: ReActRun,
    *,
    report_chars: int,
    claim_digest: int,
) -> list[ChatMessage]:
    """Build the messages that request one structured review."""
    sub_topics = (
        "\n".join(f"- {title}" for title in task.sub_topics)
        or "(none planned)"
    )
    sections = [
        f"## Research question\n{task.instruction}",
        (
            "## Report under review\n"
            f"{_clamp_report(task.report, limit=report_chars)}"
        ),
        f"## Sub-topics planned\n{sub_topics}",
        (
            "## Claim verdicts\n"
            f"{render_claim_digest(list(task.claims)[:claim_digest])}"
        ),
        f"## Source quality\n{render_source_quality(task.sources)}",
        (
            "## Recorded problems\n"
            f"{task.error_count} error(s) were recorded during this pass."
        ),
        (
            "## Spot checks\n"
            f"{render_evidence(run, limit=CRITIC_EVIDENCE_CHARS)}"
        ),
        f"## Response contract\n{CRITIQUE_INSTRUCTION}",
    ]
    return [
        ChatMessage(role="developer", content=CRITIC_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def critique_provider_error(error: Exception) -> ResearchError:
    """Record that the review call could not reach the provider.

    Non-recoverable: no review of this report exists. The run still ends
    with a routing decision, because a graph with no critique cannot route
    at all.
    """
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_review_provider_error",
        message=(
            "The model provider failed while the report was reviewed; the "
            "research pass was ended rather than repeated."
        ),
        recoverable=False,
        details={"exception_type": type(error).__name__},
    )


def missing_report_error() -> ResearchError:
    """Warn that there was no report to review."""
    return agent_error(
        agent_name=CRITIC_NAME,
        error_type="critic_missing_report",
        message="No report was available to review.",
    )


def critique_started_event(
    *,
    iteration: int,
    max_iterations: int,
    claim_count: int,
    has_report: bool,
) -> ResearchEvent:
    """Announce that the review began, before any provider call."""
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.started",
        message="Report review started.",
        metadata={
            "iteration": iteration,
            "max_iterations": max_iterations,
            "claim_count": claim_count,
            "has_report": has_report,
        },
    )


def critique_completed_event(
    critique: Critique,
    run: ReActRun,
    *,
    reason: str,
    iteration: int,
    max_iterations: int,
) -> ResearchEvent:
    """Report the score, the counts, and the routing recommendation.

    ``reason`` is a ``ROUTING_REASONS`` key, never provider text, so a
    consumer can group runs by *why* they continued or stopped rather than
    parsing a rationale.
    """
    return agent_event(
        agent_name=CRITIC_NAME,
        event_type="critic.critique.completed",
        message="Report review complete.",
        metadata={
            "score": critique.score,
            "gap_count": len(critique.gaps),
            "unsupported_claim_count": len(critique.unsupported_claims),
            "recommended_query_count": len(critique.recommended_queries),
            "should_continue": critique.should_continue,
            "reason": reason,
            "iteration": iteration,
            "max_iterations": max_iterations,
            "tool_calls": run.tool_calls,
            "stop_reason": run.stop_reason,
        },
    )


class CriticAgent(BaseAgent[Critique]):
    """Review the report, score it, and recommend a route.

    ``run`` is overridden to emit progress events and to skip the
    spot-check loop when there is no report or no tool budget; everything
    below it — bounds, tracing, tool execution, scratchpad writes — is
    still the shared runtime's.
    """

    name = CRITIC_NAME
    description = "Judge the report and recommend whether research continues."
    allowed_tools = ("web_search", "query_memory")

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        report_chars: int = CRITIC_REPORT_CHARS,
        claim_digest: int = CRITIC_CLAIM_DIGEST,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if report_chars < 1:
            raise ValueError("report_chars must be at least 1")
        if claim_digest < 1:
            raise ValueError("claim_digest must be at least 1")
        self._report_chars = report_chars
        self._claim_digest = claim_digest

    @property
    def output_schema(self) -> type[Critique]:
        """The validated critique. Never sent to the provider.

        ``review`` asks for ``CritiqueDraft`` instead, because ``Critique``
        carries ``CriticScore`` bounds and a non-blank rationale that do not
        survive strict JSON schema conversion. Do not route this agent
        through ``complete_output``.
        """
        return Critique

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return CRITIC_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> CritiqueTask:
        """Bind this review to the report and the remaining budget."""
        return CritiqueTask(
            instruction=state.original_question,
            report=state.report or "",
            iteration=state.iteration,
            max_iterations=state.max_iterations,
            claims=list(state.verified_claims),
            sources=list(state.evaluated_sources),
            sub_topics=[sub_topic.title for sub_topic in state.sub_topics],
            error_count=len(state.errors),
        )

    async def review(
        self,
        task: CritiqueTask,
        run: ReActRun,
    ) -> tuple[Critique, str, list[ResearchError], bool]:
        """Judge one report from one finished spot-check loop.

        Returns ``(critique, reason, errors, provider_failed)``. No provider
        call is made when there is no report, so a score is never invented
        over an empty review.
        """
        if not task.report.strip():
            critique, reason = fallback_critique(
                reason="missing_report",
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return critique, reason, [missing_report_error()], False

        try:
            draft = await self.provider.complete_structured(
                critique_messages(
                    task,
                    run,
                    report_chars=self._report_chars,
                    claim_digest=self._claim_digest,
                ),
                CritiqueDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            critique, reason = fallback_critique(
                reason="provider_unavailable",
                iteration=task.iteration,
                max_iterations=task.max_iterations,
            )
            return critique, reason, [critique_provider_error(error)], True

        critique, reason = build_critique(
            draft,
            iteration=task.iteration,
            max_iterations=task.max_iterations,
        )
        return critique, reason, [], False

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> Critique | None:
        """Adapt ``review`` to the ``BaseAgent`` hook.

        ``run`` calls ``review`` directly so it can keep the routing reason
        and the errors this hook signature has nowhere to return.
        """
        if not isinstance(task, CritiqueTask):
            raise AgentConfigurationError(
                "CriticAgent.finalize requires a CritiqueTask"
            )
        critique, _, _, _ = await self.review(task, run)
        return critique

    def state_update(
        self,
        result: Critique | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """The critique and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["critique"] = result
        return update

    async def _spot_check(self, task: CritiqueTask) -> ReActRun:
        """Run one bounded ReAct loop inside the caller's agent span.

        The scratchpad is cleared first: notes from a previous iteration's
        review are noise in this one's prompt.
        """
        self.scratchpad.clear()
        toolset = self.toolset

        async def decide(
            iteration: int,
            steps: Sequence[ReActStep],
        ) -> ReActDecision:
            del steps
            return await self.provider.complete_structured(
                render_react_messages(
                    system_prompt=self.system_prompt(task),
                    task=task,
                    descriptors=toolset.descriptors(),
                    scratchpad=self.scratchpad.recent(
                        self.config.prompt_context_entries
                    ),
                    iteration=iteration,
                    max_iterations=self.config.max_iterations,
                ),
                ReActDecision,
                agent_name=self.name,
            )

        react = await run_react_loop(
            agent_name=self.name,
            tracker=self.tracker,
            tools=toolset,
            decide=decide,
            max_iterations=self.config.max_iterations,
            tool_budget=self.config.tool_budget,
            on_step=self._record_step,
            is_sufficient=self.is_sufficient,
            summary_limit=self.config.observation_summary_chars,
        )
        return react.model_copy(
            update={"errors": [*react.errors, *self.scratchpad.drain_errors()]}
        )

    async def run(self, state: ResearchState) -> AgentRun[Critique]:
        """Spot-check what is worth checking, then score and route."""
        task = self.build_task(state)
        has_report = bool(task.report.strip())
        events: list[ResearchEvent] = [
            critique_started_event(
                iteration=task.iteration,
                max_iterations=task.max_iterations,
                claim_count=len(task.claims),
                has_report=has_report,
            )
        ]
        errors: list[ResearchError] = []

        async with self.tracker.agent_span(self.name) as span:
            if has_report and self.config.tool_budget > 0:
                react = await self._spot_check(task)
            else:
                # Nothing to check against (no report) or nothing to check
                # with (no tool budget): spending a provider call here would
                # buy no information the review could use.
                react = ReActRun(agent_name=self.name, stop_reason="finished")
            errors.extend(react.errors)
            critique, reason, review_errors, provider_failed = await self.review(
                task, react
            )
            errors.extend(review_errors)
            if provider_failed:
                # Mirror the loop-level provider_error path so a caller
                # reading react.succeeded never sees "finished" over an
                # abort that happened during the review.
                react = react.model_copy(update={"stop_reason": "provider_error"})
            react = react.model_copy(update={"errors": errors})
            events.append(
                critique_completed_event(
                    critique,
                    react,
                    reason=reason,
                    iteration=task.iteration,
                    max_iterations=task.max_iterations,
                )
            )
            span.set_outputs(
                {
                    "agent_name": self.name,
                    "score": critique.score,
                    "gap_count": len(critique.gaps),
                    "should_continue": critique.should_continue,
                    "reason": reason,
                    "tool_calls": react.tool_calls,
                    "stop_reason": react.stop_reason,
                }
            )

        return AgentRun(
            agent_name=self.name,
            result=critique,
            react=react,
            errors=errors,
            state_update={
                **self.state_update(critique, react),
                "events": events,
            },
        )
