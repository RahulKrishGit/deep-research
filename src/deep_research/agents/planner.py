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

from deep_research.agents.prompts import AgentTask
from deep_research.agents.steps import ReActRun, summarize_text
from deep_research.providers import ChatMessage
from deep_research.utils.types import ContractModel, SubTopic

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


def _invalid_fields(error: ValidationError) -> str:
    """Name the fields a validation failure touched, without provider text."""
    fields = sorted(
        {
            str(detail["loc"][0])
            for detail in error.errors()
            if detail["loc"]
        }
    )
    return ", ".join(fields) or "unknown"


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
