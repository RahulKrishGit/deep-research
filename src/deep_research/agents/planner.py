"""The Planner: turn one research question into a validated research plan.

The provider is asked for ``ResearchPlanDraft``, not for a plan of real
``SubTopic`` values. ``SubTopic`` declares ``Field(min_length=1)`` on its
string and list fields, which Pydantic renders as ``minLength``/``minItems``
— keywords outside OpenAI's strict structured-output subset. The draft
models carry no constraints at all, and ``validate_plan_draft`` applies the
domain rules locally where their failures can be turned into a repair prompt.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field, ValidationError

from deep_research.agents.base import AgentRun, BaseAgent
from deep_research.agents.errors import PlanningError
from deep_research.agents.events import agent_event
from deep_research.agents.prompts import AgentTask, render_memory_guidance
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.agents.validation import _invalid_fields
from deep_research.providers import ChatMessage, ProviderError
from deep_research.utils.types import (
    ContractModel,
    MemorySnapshot,
    ResearchEvent,
    ResearchState,
    ResearchStateUpdate,
    SubTopic,
)

PLANNER_NAME = "planner"
MIN_SUB_TOPICS = 3
MAX_SUB_TOPICS = 7

PLANNER_SYSTEM_PROMPT = (
    "You are the planner of a multi-agent research system. Your job is to "
    "turn one research question into a plan of distinct sub-topics that "
    "together answer it.\n"
    "Use query_memory to recall what previous sessions already learned. Use "
    "web_search only to scope unfamiliar terminology — a later agent "
    "gathers the evidence, so do not research the question here.\n"
    "Finish as soon as you understand the shape of the question."
)

PLAN_INSTRUCTION = (
    f"Produce a research plan of between {MIN_SUB_TOPICS} and "
    f"{MAX_SUB_TOPICS} distinct sub-topics that together answer the "
    "research question.\n"
    "Every sub-topic needs a title, a rationale explaining why answering it "
    "is necessary, at least one concrete web search query, at least one "
    "success criterion describing what evidence would settle it, and a "
    "priority where 1 is the most important.\n"
    "Two sub-topics must never share a title."
)


class SubTopicDraft(ContractModel):
    """One model-proposed sub-topic, before domain validation.

    Deliberately declares no ``Field`` constraints: this model is converted
    to a strict OpenAI JSON schema, which rejects ``minLength`` and
    ``minItems``. Constraints live in ``SubTopic``.
    """

    title: str
    rationale: str
    search_queries: list[str]
    success_criteria: list[str]
    priority: int


class ResearchPlanDraft(ContractModel):
    """The provider-facing plan schema."""

    sub_topics: list[SubTopicDraft]


class ResearchPlan(ContractModel):
    """The validated plan ``PlannerAgent`` produces.

    Never sent to the provider — ``ResearchPlanDraft`` is — so its size
    bounds are free to be real constraints.
    """

    sub_topics: list[SubTopic] = Field(
        min_length=MIN_SUB_TOPICS,
        max_length=MAX_SUB_TOPICS,
    )
    repair_attempted: bool = False


def _normalized_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def validate_plan_draft(
    draft: ResearchPlanDraft,
) -> tuple[list[SubTopic], list[str]]:
    """Convert a model plan into ``SubTopic`` values, listing every problem.

    Returns the sub-topics that validated and a list of problem strings.
    Problem text is generated here and never copied from provider output, so
    it is safe to place in a repair prompt and in ``PlanningError.problems``.
    """
    validated: list[tuple[int, SubTopic]] = []
    problems: list[str] = []

    for index, item in enumerate(draft.sub_topics, start=1):
        try:
            validated.append(
                (index, SubTopic.model_validate(item.model_dump()))
            )
        except ValidationError as error:
            problems.append(
                f"sub-topic {index} is invalid: check these fields: "
                f"{_invalid_fields(error)}"
            )

    seen: dict[str, int] = {}
    for index, sub_topic in validated:
        key = _normalized_title(sub_topic.title)
        first = seen.get(key)
        if first is None:
            seen[key] = index
        else:
            problems.append(
                f"sub-topics {first} and {index} repeat the same title; "
                "every sub-topic must be distinct"
            )

    sub_topics = [sub_topic for _, sub_topic in validated]
    count = len(sub_topics)
    if count < MIN_SUB_TOPICS or count > MAX_SUB_TOPICS:
        problems.append(
            f"the plan has {count} valid sub-topics; produce between "
            f"{MIN_SUB_TOPICS} and {MAX_SUB_TOPICS}"
        )

    return sub_topics, problems


def format_plan_problems(problems: Sequence[str]) -> str:
    """Render plan problems as the corrective instruction for one repair."""
    listed = "\n".join(f"- {problem}" for problem in problems)
    return (
        "The previous plan was rejected. Fix every problem listed below and "
        f"return a corrected plan.\n{listed}"
    )


def _render_notes(run: ReActRun) -> str:
    """Render what the scoping loop actually learned, one line each."""
    lines = [
        f"- {summarize_text(step.observation.summary)}"
        for step in run.steps
        if step.observation is not None and step.observation.success
    ]
    if run.final_answer is not None:
        lines.append(f"- {summarize_text(run.final_answer)}")
    return "\n".join(lines) or "(no scoping notes)"


def plan_messages(
    task: AgentTask,
    run: ReActRun,
    *,
    repair: str | None = None,
) -> list[ChatMessage]:
    """Build the messages that request one structured plan draft."""
    sections = [f"## Research question\n{task.instruction}"]
    if task.guidance.strip():
        sections.append(f"## Context\n{task.guidance}")
    sections.append(f"## Scoping notes\n{_render_notes(run)}")
    sections.append(f"## Plan requirements\n{PLAN_INSTRUCTION}")
    if repair is not None:
        sections.append(f"## Repair\n{repair}")
    return [
        ChatMessage(role="developer", content=PLANNER_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n\n".join(sections)),
    ]


def planning_started_event(state: ResearchState) -> ResearchEvent:
    """Announce that planning began, before any provider call."""
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.started",
        message="Planning started.",
        metadata={
            "iteration": state.iteration,
            "min_sub_topics": MIN_SUB_TOPICS,
            "max_sub_topics": MAX_SUB_TOPICS,
        },
    )


def memory_recalled_event(memory_context: MemorySnapshot) -> ResearchEvent:
    """Report how much long-term memory the session started with."""
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.memory.recalled",
        message="Memory recall complete.",
        metadata={
            "recalled_findings": len(memory_context.similar_findings),
            "known_source_reputations": len(
                memory_context.known_source_reputations
            ),
            "suggested_strategies": len(memory_context.suggested_strategies),
        },
    )


def planning_completed_event(outcome: AgentRun["ResearchPlan"]) -> ResearchEvent:
    """Report the finished plan's size and how the scoping loop stopped."""
    plan = outcome.result
    return agent_event(
        agent_name=PLANNER_NAME,
        event_type="planner.planning.completed",
        message="Planning complete.",
        metadata={
            "sub_topic_count": 0 if plan is None else len(plan.sub_topics),
            "repair_attempted": False if plan is None else plan.repair_attempted,
            "stop_reason": outcome.react.stop_reason,
            "iterations": outcome.react.iterations,
            "tool_calls": outcome.react.tool_calls,
        },
    )


class PlannerAgent(BaseAgent[ResearchPlan]):
    """Convert ``original_question`` into 3-7 distinct, prioritized sub-topics.

    The ReAct loop is for scoping only — recalling prior findings and
    resolving unfamiliar terminology. The plan itself is produced in
    ``finalize`` by a structured-output call over what the loop learned.
    """

    name = PLANNER_NAME
    description = "Turn a research question into a validated research plan."
    allowed_tools = ("query_memory", "web_search")

    @property
    def output_schema(self) -> type[ResearchPlan]:
        """The validated plan. Never sent to the provider.

        ``finalize`` asks for ``ResearchPlanDraft`` instead, because
        ``ResearchPlan`` nests ``SubTopic``, whose ``Field`` constraints do
        not survive strict JSON schema conversion. Do not route this agent
        through ``complete_output``.
        """
        return ResearchPlan

    def system_prompt(self, task: AgentTask) -> str:
        del task
        return PLANNER_SYSTEM_PROMPT

    def build_task(self, state: ResearchState) -> AgentTask:
        return AgentTask(
            instruction=state.original_question,
            guidance=render_memory_guidance(state.memory_context),
        )

    async def _request_plan(
        self,
        task: AgentTask,
        run: ReActRun,
        *,
        repair: str | None = None,
    ) -> tuple[list[SubTopic], list[str]]:
        try:
            draft = await self.provider.complete_structured(
                plan_messages(task, run, repair=repair),
                ResearchPlanDraft,
                agent_name=self.name,
            )
        except ProviderError as error:
            raise PlanningError(
                "The planner could not reach the model provider while a "
                "plan was requested.",
                problems=["the model provider failed while the plan was requested"],
            ) from error
        return validate_plan_draft(draft)

    async def finalize(
        self,
        task: AgentTask,
        run: ReActRun,
    ) -> ResearchPlan | None:
        """Request a plan, repair it at most once, or fail the session."""
        if not run.succeeded:
            raise PlanningError(
                "The planner could not reach the model provider.",
                problems=[
                    "the model provider failed before a plan was requested"
                ],
            )

        sub_topics, problems = await self._request_plan(task, run)
        if not problems:
            return ResearchPlan(sub_topics=sub_topics)

        sub_topics, problems = await self._request_plan(
            task, run, repair=format_plan_problems(problems)
        )
        if problems:
            raise PlanningError(
                "The planner could not produce a valid research plan after "
                "one repair attempt.",
                problems=problems,
            )
        return ResearchPlan(sub_topics=sub_topics, repair_attempted=True)

    def state_update(
        self,
        result: ResearchPlan | None,
        run: ReActRun,
    ) -> ResearchStateUpdate:
        update: ResearchStateUpdate = {"errors": list(run.errors)}
        if result is not None:
            update["sub_topics"] = list(result.sub_topics)
        return update

    async def run(self, state: ResearchState) -> AgentRun[ResearchPlan]:
        """Run the inherited loop, bracketed by planning progress events."""
        events = [
            planning_started_event(state),
            memory_recalled_event(state.memory_context),
        ]
        outcome = await super().run(state)
        events.append(planning_completed_event(outcome))
        return AgentRun(
            agent_name=outcome.agent_name,
            result=outcome.result,
            react=outcome.react,
            errors=outcome.errors,
            state_update={**outcome.state_update, "events": events},
        )
