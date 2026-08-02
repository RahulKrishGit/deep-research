"""The Researcher: gather source-backed findings for planned sub-topics.

Like the Planner, this module never sends a domain type to the provider.
``FindingDraft`` mirrors ``Finding`` with plain field types so it survives
strict JSON schema conversion; ``build_findings`` stamps the drafts with the
sub-topic and extraction time the model must not be trusted to supply.

Priority convention: lower is more important. Priority 1 is the most
important sub-topic, and ``HIGH_PRIORITY_THRESHOLD`` is the largest value
still considered high priority.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from pydantic import Field, ValidationError

from deep_research.agents.base import AgentRun, BaseAgent, StructuredCompleter
from deep_research.agents.errors import AgentConfigurationError, agent_error
from deep_research.agents.events import agent_event
from deep_research.agents.prompts import (
    AgentTask,
    render_memory_guidance,
    render_react_messages,
)
from deep_research.agents.react import run_react_loop
from deep_research.agents.steps import (
    ReActDecision,
    ReActRun,
    ReActStep,
    StopReason,
    summarize_text,
)
from deep_research.agents.validation import invalid_fields
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.providers import ChatMessage, OpenAIProviderError
from deep_research.tools.base import BaseTool, ToolResult
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import (
    ContractModel,
    Finding,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    SubTopic,
)

RESEARCHER_NAME = "researcher"
HIGH_PRIORITY_THRESHOLD = 2
DEFAULT_MAX_SUB_TOPICS = 3
DEFAULT_EVIDENCE_CHARS = 4000

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

RESEARCHER_SYSTEM_PROMPT = (
    "You are the researcher of a multi-agent research system. You gather "
    "evidence for exactly one sub-topic at a time.\n"
    "Use web_search to find candidate sources, web_scraper to read a "
    "promising page, document_reader for PDFs and data files, query_memory "
    "to avoid repeating research a previous session already did, and "
    "save_to_memory to keep a high-value finding for future sessions.\n"
    "Every claim you report must come from a source you actually retrieved "
    "in this loop. If a tool fails, try another query or another source "
    "rather than giving up.\n"
    "Finish once the sub-topic's success criteria are met, or once no "
    "further source is worth retrieving."
)

EXTRACTION_SYSTEM_PROMPT = (
    "You extract findings from a completed research transcript. Report only "
    "what the retrieved sources state, and attribute every finding to the "
    "exact source URL and title it came from. Return an empty list rather "
    "than inventing a source. Confidence is a number between 0 and 1."
)


class FindingDraft(ContractModel):
    """One model-extracted finding, before domain validation.

    Declares no ``Field`` constraints for the same reason as
    ``planner.SubTopicDraft``: it is converted to a strict OpenAI JSON
    schema. ``extracted_at`` and ``related_sub_topic`` are deliberately
    absent — this project stamps those, not the model.
    """

    content: str
    source_url: str
    source_title: str
    confidence: float


class SubTopicFindingsDraft(ContractModel):
    """The provider-facing extraction schema for one sub-topic."""

    findings: list[FindingDraft]


class ResearchFindings(ContractModel):
    """The validated findings ``ResearcherAgent`` produces.

    Never sent to the provider — ``SubTopicFindingsDraft`` is. Do not route
    this agent through ``complete_output``.
    """

    findings: list[Finding] = Field(default_factory=list)


class SubTopicTask(AgentTask):
    """An ``AgentTask`` bound to the sub-topic its loop researches.

    Carrying the sub-topic on the task is what lets ``finalize(task, run)``
    know which sub-topic it is finalizing without the agent holding mutable
    state across await points.
    """

    sub_topic: SubTopic
    existing_sources: list[str] = Field(default_factory=list)


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def select_sub_topics(
    state: ResearchState,
    *,
    max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
) -> list[SubTopic]:
    """Order sub-topics by Critic-flagged gaps first, then by priority.

    A sub-topic counts as gap-flagged when its normalized title appears
    inside the concatenated, normalized text of ``critique.gaps``. Ties
    resolve by ``priority`` ascending (1 is most important), then by the
    order the planner produced.
    """
    if max_sub_topics < 1:
        raise ValueError("max_sub_topics must be at least 1")
    critique = state.critique
    gap_text = (
        " ".join(_normalized(gap) for gap in critique.gaps)
        if critique is not None
        else ""
    )

    def sort_key(item: tuple[int, SubTopic]) -> tuple[int, int, int]:
        index, sub_topic = item
        title = _normalized(sub_topic.title)
        flagged = 0 if title and title in gap_text else 1
        return (flagged, sub_topic.priority, index)

    ordered = sorted(enumerate(state.sub_topics), key=sort_key)
    return [sub_topic for _, sub_topic in ordered[:max_sub_topics]]


def is_high_priority(
    sub_topic: SubTopic,
    *,
    threshold: int = HIGH_PRIORITY_THRESHOLD,
) -> bool:
    """True when an empty result for this sub-topic is worth warning about."""
    return sub_topic.priority <= threshold


def existing_sources_for(
    state: ResearchState,
    sub_topic: SubTopic,
) -> list[str]:
    """Return the source URLs already recorded for one sub-topic, in order."""
    seen: list[str] = []
    for finding in state.raw_findings:
        if finding.related_sub_topic != sub_topic.title:
            continue
        if finding.source_url not in seen:
            seen.append(finding.source_url)
    return seen


# Precedence for the merged ``stop_reason``, most urgent first. A caller
# only ever sees one stop reason for the whole merged run, so a reason that
# demands attention (a hard failure, or a budget that ran out) must never be
# masked by a later sub-topic that happened to finish cleanly. "finished" is
# the least informative outcome and sorts last for exactly that reason;
# "sufficient" is a deliberate early stop and outranks it but still yields
# to any budget or error condition.
_STOP_REASON_PRECEDENCE: tuple[StopReason, ...] = (
    "provider_error",
    "tool_budget_exhausted",
    "max_iterations",
    "sufficient",
    "finished",
)


def merge_react_runs(
    agent_name: str,
    runs: Sequence[ReActRun],
) -> ReActRun:
    """Fold per-sub-topic loops into one run record for ``AgentRun.react``.

    ``stop_reason`` is picked by ``_STOP_REASON_PRECEDENCE``: the highest-
    priority reason present across every run wins, so a real stop condition
    (an error, or an exhausted budget) is never masked by a later sub-topic
    that simply finished. Per-sub-topic stop reasons stay in the emitted
    events. ``final_answer`` joins every non-``None`` final answer across the
    merged runs, in order, separated by blank lines — sub-topic loops each
    contribute their own answer, and none should be silently dropped.
    """
    if not runs:
        return ReActRun(agent_name=agent_name, stop_reason="finished")

    steps: list[ReActStep] = []
    errors: list[ResearchError] = []
    final_answers: list[str] = []
    for run in runs:
        steps.extend(run.steps)
        errors.extend(run.errors)
        if run.final_answer is not None:
            final_answers.append(run.final_answer)
    final_answer = "\n\n".join(final_answers) if final_answers else None

    reasons = {run.stop_reason for run in runs}
    stop_reason: StopReason = next(
        reason for reason in _STOP_REASON_PRECEDENCE if reason in reasons
    )
    return ReActRun(
        agent_name=agent_name,
        steps=steps,
        stop_reason=stop_reason,
        iterations=sum(run.iterations for run in runs),
        tool_calls=sum(run.tool_calls for run in runs),
        final_answer=final_answer,
        errors=errors,
    )


def render_session_guidance(state: ResearchState) -> str:
    """Render Critic feedback and recalled memory as shared run context."""
    sections: list[str] = []
    critique = state.critique
    if critique is not None:
        lines = ["The critic asked for another research pass."]
        if critique.gaps:
            lines.append("Gaps to close:")
            lines.extend(f"- {summarize_text(gap)}" for gap in critique.gaps)
        if critique.recommended_queries:
            lines.append("Run these recommended queries first:")
            lines.extend(
                f"- {summarize_text(query)}"
                for query in critique.recommended_queries
            )
        if critique.unsupported_claims:
            lines.append("Find sources for these unsupported claims:")
            lines.extend(
                f"- {summarize_text(claim)}"
                for claim in critique.unsupported_claims
            )
        sections.append("\n".join(lines))

    memory_section = render_memory_guidance(state.memory_context)
    if memory_section:
        sections.append(memory_section)
    return "\n\n".join(sections)


def render_sub_topic_guidance(
    sub_topic: SubTopic,
    existing_sources: Sequence[str],
) -> str:
    """Render one sub-topic's brief, including sources already collected."""
    lines = [
        f"Sub-topic: {sub_topic.title}",
        f"Why it matters: {sub_topic.rationale}",
        f"Priority: {sub_topic.priority} (1 is most important)",
        "Suggested search queries:",
        *(f"- {query}" for query in sub_topic.search_queries),
        "This sub-topic is done when:",
        *(f"- {criterion}" for criterion in sub_topic.success_criteria),
    ]
    if existing_sources:
        lines.append(
            "Sources already collected for this sub-topic — do not repeat "
            "them:"
        )
        lines.extend(f"- {source}" for source in existing_sources)
    return "\n".join(lines)


# Read tools that can carry evidence, mapped to the payload key that holds
# it. ``save_to_memory`` is deliberately absent: it is a write, never
# evidence, and a successful call to it must never count as "retrieved".
_EVIDENCE_PAYLOAD_KEYS: dict[str, str] = {
    "web_search": "results",
    "web_scraper": "text",
    "document_reader": "chunks",
    "query_memory": "matches",
}


def _successful_result(step: ReActStep) -> ToolResult | None:
    """The step's tool result, or ``None`` unless the call succeeded."""
    result = step.tool_result
    if result is None or not result.success:
        return None
    return result


def _has_evidence(step: ReActStep) -> bool:
    """True when a successful call actually returned a non-empty payload.

    A tool can return ``success=True`` with nothing usable inside it —
    ``query_memory`` on a miss, ``web_search`` with no hits, an empty
    scrape — and ``save_to_memory`` never carries evidence at all. Counting
    any of those as "retrieved" would let the extraction call run against
    an evidence section with nothing in it, which is exactly the
    hallucination-pressure case the caller guards against.
    """
    result = _successful_result(step)
    if result is None:
        return False
    key = _EVIDENCE_PAYLOAD_KEYS.get(result.tool_name)
    if key is None:
        return False
    data = result.data
    value = data.get(key) if isinstance(data, dict) else None
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return False


def render_evidence(run: ReActRun, *, limit: int) -> str:
    """Render every successful tool payload, each clamped to ``limit`` chars.

    Written as an explicit loop rather than a comprehension: the payload
    dump has to be summarized before interpolation, and Python 3.11
    f-strings cannot hold a multi-line call expression.
    """
    lines: list[str] = []
    for step in run.steps:
        result = _successful_result(step)
        if result is None:
            continue
        payload = json.dumps(result.data, default=str, ensure_ascii=False)
        summary = summarize_text(payload, limit=limit)
        lines.append(f"- [{result.tool_name}] {summary}")
    return "\n".join(lines) or "(no evidence retrieved)"


def extraction_messages(
    task: SubTopicTask,
    run: ReActRun,
    *,
    evidence_chars: int,
) -> list[ChatMessage]:
    """Build the messages that extract findings from one finished loop."""
    criteria = "\n".join(
        f"- {criterion}" for criterion in task.sub_topic.success_criteria
    )
    sections = [
        f"## Sub-topic\n{task.sub_topic.title}",
        f"## Success criteria\n{criteria}",
        f"## Retrieved evidence\n{render_evidence(run, limit=evidence_chars)}",
        (
            "## Response contract\nReturn one finding per distinct, "
            "source-backed claim. Use the exact source_url and source_title "
            "from the evidence above. Return an empty list when the evidence "
            "supports nothing."
        ),
    ]
    return [
        ChatMessage(role="developer", content=EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def build_findings(
    draft: SubTopicFindingsDraft,
    *,
    sub_topic: SubTopic,
    extracted_at: str,
) -> tuple[list[Finding], list[str]]:
    """Stamp drafts into ``Finding`` values, naming the ones that were dropped.

    Rejection reasons are generated here and never copied from provider
    output, so they are safe to record in ``ResearchError.details``.
    """
    findings: list[Finding] = []
    rejected: list[str] = []
    for index, item in enumerate(draft.findings, start=1):
        try:
            findings.append(
                Finding(
                    content=item.content,
                    source_url=item.source_url,
                    source_title=item.source_title,
                    extracted_at=extracted_at,
                    confidence=item.confidence,
                    related_sub_topic=sub_topic.title,
                )
            )
        except ValidationError as error:
            rejected.append(f"finding {index}: invalid {invalid_fields(error)}")
    return findings, rejected


def sub_topic_started_event(
    sub_topic: SubTopic,
    *,
    index: int,
    existing_sources: int,
) -> ResearchEvent:
    """Announce that one sub-topic's loop is about to run."""
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.sub_topic.started",
        message=f"Researching sub-topic {index}.",
        metadata={
            "sub_topic": sub_topic.title,
            "priority": sub_topic.priority,
            "index": index,
            "existing_sources": existing_sources,
        },
    )


def tool_call_events(
    sub_topic: SubTopic,
    run: ReActRun,
) -> list[ResearchEvent]:
    """Report one event per tool call the sub-topic's loop made."""
    events: list[ResearchEvent] = []
    for step in run.steps:
        observation = step.observation
        if observation is None:
            continue
        events.append(
            agent_event(
                agent_name=RESEARCHER_NAME,
                event_type="researcher.tool_call",
                message=f"{observation.tool_name} call completed.",
                metadata={
                    "sub_topic": sub_topic.title,
                    "tool": observation.tool_name,
                    "iteration": step.iteration,
                    "success": observation.success,
                    "error_type": observation.error_type,
                },
            )
        )
    return events


def sub_topic_completed_event(
    sub_topic: SubTopic,
    run: ReActRun,
    *,
    index: int,
    findings: int,
) -> ResearchEvent:
    """Report one sub-topic's stop reason, counts, and finding total."""
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.sub_topic.completed",
        message=f"Sub-topic {index} complete.",
        metadata={
            "sub_topic": sub_topic.title,
            "index": index,
            "stop_reason": run.stop_reason,
            "iterations": run.iterations,
            "tool_calls": run.tool_calls,
            "findings": findings,
        },
    )


def research_completed_event(
    *,
    sub_topics_researched: int,
    findings: int,
) -> ResearchEvent:
    """Report the whole research pass."""
    return agent_event(
        agent_name=RESEARCHER_NAME,
        event_type="researcher.research.completed",
        message="Research pass complete.",
        metadata={
            "sub_topics_researched": sub_topics_researched,
            "findings": findings,
        },
    )


def no_findings_error(sub_topic: SubTopic, run: ReActRun) -> ResearchError:
    """Warn that a high-priority sub-topic produced nothing citable."""
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_sub_topic_without_findings",
        message=(
            "A high-priority sub-topic produced no findings; the report will "
            "be incomplete for it."
        ),
        details={
            "sub_topic": sub_topic.title,
            "priority": sub_topic.priority,
            "stop_reason": run.stop_reason,
            "tool_calls": run.tool_calls,
        },
    )


def extraction_provider_error(
    run: ReActRun,
    error: Exception,
) -> ResearchError:
    """Record that a sub-topic's extraction call could not reach the provider.

    Non-recoverable: the caller must stop researching remaining sub-topics,
    mirroring the ReAct-loop-level ``provider_error`` path. ``details``
    carries only ``exception_type`` and counts, never ``str(error)`` — the
    same redaction discipline ``react.py`` and ``planner.py`` follow.
    """
    return agent_error(
        agent_name=RESEARCHER_NAME,
        error_type="researcher_extraction_provider_error",
        message=(
            "The model provider failed while extracting findings for a "
            "sub-topic; the research pass stopped before further "
            "sub-topics were researched."
        ),
        recoverable=False,
        details={
            "exception_type": type(error).__name__,
            "iterations": run.iterations,
            "tool_calls": run.tool_calls,
        },
    )


class ResearcherAgent(BaseAgent[ResearchFindings]):
    """Run one bounded ReAct loop per selected sub-topic and extract findings.

    ``run`` is overridden because the spec requires a loop *per sub-topic*,
    which the single-loop ``BaseAgent.run`` cannot express. Everything below
    ``run`` — bounds, tracing, tool execution, scratchpad writes — is still
    the shared runtime's.
    """

    name = RESEARCHER_NAME
    description = "Gather source-backed findings for planned sub-topics."
    allowed_tools = (
        "web_search",
        "web_scraper",
        "document_reader",
        "query_memory",
        "save_to_memory",
    )

    def __init__(
        self,
        *,
        provider: StructuredCompleter,
        tracker: Tracker,
        scratchpad: ScratchpadMemory,
        tools: Sequence[BaseTool] = (),
        config: AgentRuntimeConfig | None = None,
        max_sub_topics: int = DEFAULT_MAX_SUB_TOPICS,
        high_priority_threshold: int = HIGH_PRIORITY_THRESHOLD,
        evidence_chars: int = DEFAULT_EVIDENCE_CHARS,
        clock: Clock = _utc_now,
    ) -> None:
        super().__init__(
            provider=provider,
            tracker=tracker,
            scratchpad=scratchpad,
            tools=tools,
            config=config,
        )
        if max_sub_topics < 1:
            raise ValueError("max_sub_topics must be at least 1")
        if high_priority_threshold < 1:
            raise ValueError("high_priority_threshold must be at least 1")
        if evidence_chars < 1:
            raise ValueError("evidence_chars must be at least 1")
        probe = clock()
        if probe.tzinfo is None or probe.utcoffset() is None:
            raise AgentConfigurationError(
                "ResearcherAgent clock must return a timezone-aware "
                "datetime; got a naive datetime instead"
            )
        self._max_sub_topics = max_sub_topics
        self._high_priority_threshold = high_priority_threshold
        self._evidence_chars = evidence_chars
        self._clock = clock

    @property
    def output_schema(self) -> type[ResearchFindings]:
        """The validated findings. Never sent to the provider.

        ``extract_findings`` asks for ``SubTopicFindingsDraft`` instead,
        because ``Finding`` carries ``Field`` constraints that do not survive
        strict JSON schema conversion. Do not route this agent through
        ``complete_output``.
        """
        return ResearchFindings

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return RESEARCHER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> AgentTask:
        """Describe the run as a whole. ``sub_topic_task`` narrows it."""
        return AgentTask(
            instruction=state.original_question,
            guidance=render_session_guidance(state),
        )

    def sub_topic_task(
        self,
        base: AgentTask,
        sub_topic: SubTopic,
        existing_sources: Sequence[str],
    ) -> SubTopicTask:
        """Narrow the run-level task down to one sub-topic's loop."""
        sections = [
            section
            for section in (
                base.guidance.strip(),
                render_sub_topic_guidance(sub_topic, existing_sources),
            )
            if section
        ]
        return SubTopicTask(
            instruction=(
                f'Gather evidence for the sub-topic "{sub_topic.title}" of '
                f"the research question: {base.instruction}"
            ),
            guidance="\n\n".join(sections),
            sub_topic=sub_topic,
            existing_sources=list(existing_sources),
        )

    async def extract_findings(
        self,
        task: SubTopicTask,
        run: ReActRun,
    ) -> tuple[list[Finding], list[ResearchError], bool]:
        """Turn one finished sub-topic loop into validated findings.

        Returns nothing — and makes no provider call — when the loop stopped
        on a provider failure or retrieved no source, so the extraction step
        can never invent one.

        The third element, ``provider_failed``, is ``True`` only when the
        extraction call itself could not reach the model provider. The
        caller must treat that the same way it treats a ReAct-loop-level
        ``provider_error``: stop researching further sub-topics, but keep
        every finding already collected.
        """
        retrieved = any(_has_evidence(step) for step in run.steps)
        if not run.succeeded or not retrieved:
            return [], [], False

        try:
            draft = await self.provider.complete_structured(
                extraction_messages(
                    task, run, evidence_chars=self._evidence_chars
                ),
                SubTopicFindingsDraft,
                agent_name=self.name,
            )
        except OpenAIProviderError as error:
            return [], [extraction_provider_error(run, error)], True

        findings, rejected = build_findings(
            draft,
            sub_topic=task.sub_topic,
            extracted_at=self._clock().isoformat(),
        )
        if not rejected:
            return findings, [], False
        return findings, [
            agent_error(
                agent_name=self.name,
                error_type="researcher_invalid_finding",
                message=(
                    "Some extracted findings were malformed and were dropped."
                ),
                details={
                    "sub_topic": task.sub_topic.title,
                    "rejected": rejected,
                },
            )
        ], False

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> ResearchFindings | None:
        """Adapt ``extract_findings`` to the ``BaseAgent`` hook.

        ``run`` calls ``extract_findings`` directly so it can keep the
        recoverable errors this hook signature has nowhere to return.
        """
        if not isinstance(task, SubTopicTask):
            raise AgentConfigurationError(
                "ResearcherAgent.finalize requires a SubTopicTask"
            )
        findings, _, _ = await self.extract_findings(task, run)
        return ResearchFindings(findings=findings)

    def state_update(
        self,
        result: ResearchFindings | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        """Findings and errors only. ``run`` adds the progress events."""
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["raw_findings"] = list(result.findings)
        return update

    async def _research_sub_topic(self, task: SubTopicTask) -> ReActRun:
        """Run one bounded ReAct loop inside the caller's agent span.

        The scratchpad is cleared first: notes about the previous sub-topic
        are noise in this one's prompt. Context that genuinely carries over
        travels in ``task.guidance`` instead.
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
            update={
                "errors": [*react.errors, *self.scratchpad.drain_errors()]
            }
        )

    async def run(self, state: ResearchState) -> AgentRun[ResearchFindings]:
        """Research each selected sub-topic in its own bounded loop."""
        base_task = self.build_task(state)
        selected = select_sub_topics(
            state, max_sub_topics=self._max_sub_topics
        )
        events: list[ResearchEvent] = []
        errors: list[ResearchError] = []
        findings: list[Finding] = []
        runs: list[ReActRun] = []

        for index, sub_topic in enumerate(selected, start=1):
            existing = existing_sources_for(state, sub_topic)
            task = self.sub_topic_task(base_task, sub_topic, existing)
            events.append(
                sub_topic_started_event(
                    sub_topic, index=index, existing_sources=len(existing)
                )
            )
            async with self.tracker.agent_span(self.name) as span:
                react = await self._research_sub_topic(task)
                (
                    sub_findings,
                    extraction_errors,
                    extraction_failed,
                ) = await self.extract_findings(task, react)
                span.set_outputs(
                    {
                        "agent_name": self.name,
                        "sub_topic": sub_topic.title,
                        "stop_reason": react.stop_reason,
                        "iterations": react.iterations,
                        "tool_calls": react.tool_calls,
                        "findings": len(sub_findings),
                    }
                )

            runs.append(react)
            findings.extend(sub_findings)
            errors.extend(react.errors)
            errors.extend(extraction_errors)
            events.extend(tool_call_events(sub_topic, react))
            events.append(
                sub_topic_completed_event(
                    sub_topic,
                    react,
                    index=index,
                    findings=len(sub_findings),
                )
            )
            if not sub_findings and is_high_priority(
                sub_topic, threshold=self._high_priority_threshold
            ):
                errors.append(no_findings_error(sub_topic, react))
            if not react.succeeded or extraction_failed:
                # A provider failure — whether from the ReAct loop or from
                # extraction — is non-recoverable; the next sub-topic would
                # almost certainly repeat it at cost. Findings already
                # collected from this and prior sub-topics are kept.
                break

        if not selected:
            errors.append(
                agent_error(
                    agent_name=self.name,
                    error_type="researcher_no_sub_topics",
                    message="No sub-topics were available to research.",
                )
            )
        events.append(
            research_completed_event(
                sub_topics_researched=len(runs), findings=len(findings)
            )
        )

        merged = merge_react_runs(self.name, runs).model_copy(
            update={"errors": errors}
        )
        result = ResearchFindings(findings=findings)
        return AgentRun(
            agent_name=self.name,
            result=result,
            react=merged,
            errors=errors,
            state_update={
                **self.state_update(result, merged),
                "events": events,
            },
        )
