"""The one native tool boundary every model-directed agent crosses.

A regression guard, not a unit test of one agent. Each of the four agents that
ask a model to select a tool is run end to end through its own ``run`` — so an
agent that reintroduced a local ``decide`` closure calling
``complete_structured(..., ReActDecision, ...)`` fails here, because
``ScriptedCompleter.complete_structured`` raises for that schema.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from deep_research.agents.critic import CriticAgent, CritiqueDraft
from deep_research.agents.fact_checker import (
    ClaimDraft,
    ClaimsDraft,
    ClaimVerdictDraft,
    FactCheckerAgent,
)
from deep_research.agents.planner import (
    PlannerAgent,
    ResearchPlanDraft,
    SubTopicDraft,
)
from deep_research.agents.researcher import (
    ResearcherAgent,
    SubTopicFindingsDraft,
)
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from deep_research.utils.types import Finding, ResearchState, SubTopic
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    critic_tools,
    fact_checker_tools,
    planner_tools,
    research_tools,
)

EXTRACTED_AT = "2026-08-01T12:00:00+00:00"
FINDING_URL = "https://example.test/qec"

_PLAN = ResearchPlanDraft(
    sub_topics=[
        SubTopicDraft(
            title=f"angle number {index}",
            rationale=f"Rationale for angle number {index}.",
            search_queries=[f"quantum computing angle {index}"],
            success_criteria=[f"evidence about angle number {index}"],
            priority=index,
        )
        for index in (1, 2, 3)
    ]
)


def _finding() -> Finding:
    return Finding(
        content="Logical error rates fell below break-even.",
        source_url=FINDING_URL,
        source_title="QEC results",
        extracted_at=EXTRACTED_AT,
        confidence=0.8,
        related_sub_topic="angle number 1",
    )


def _state(**updates: object) -> ResearchState:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "original_question": "how much capacity can quantum computing reach",
        "max_iterations": 2,
    }
    payload.update(updates)
    return ResearchState.model_validate(payload)


def _planner_case() -> tuple[ResearchState, list, list]:
    return _state(), [finish("Enough context.", "Scoping is complete.")], [_PLAN]


def _researcher_case() -> tuple[ResearchState, list, list]:
    state = _state(
        sub_topics=[
            SubTopic(
                title="angle number 1",
                rationale="Rationale for angle number 1.",
                search_queries=["quantum computing angle 1"],
                success_criteria=["evidence about angle number 1"],
                priority=1,
            )
        ]
    )
    return (
        state,
        [finish("Nothing to add.", "The evidence is sufficient.")],
        [SubTopicFindingsDraft(findings=[])],
    )


def _fact_checker_case() -> tuple[ResearchState, list, list]:
    state = _state(raw_findings=[_finding()])
    return (
        state,
        [finish("Nothing independent.", "Nothing independent was retrieved.")],
        [
            ClaimsDraft(
                claims=[
                    ClaimDraft(
                        text="Logical error rates fell below break-even.",
                        source_urls=[FINDING_URL],
                    )
                ]
            ),
            ClaimVerdictDraft(
                verdict="insufficient_evidence",
                confidence=0.0,
                evidence=[],
                contradictions=[],
            ),
        ],
    )


def _critic_case() -> tuple[ResearchState, list, list]:
    state = _state(
        report="# Research report: the measured capacity is reported.",
        iteration=1,
    )
    return (
        state,
        [finish("The report is enough.", "No spot check needed.")],
        [
            CritiqueDraft(
                score=8,
                gaps=[],
                unsupported_claims=[],
                recommended_queries=[],
                rationale="The report answers the question from cited sources.",
            )
        ],
    )


TOOL_SELECTING_AGENTS: tuple[
    tuple[type, Callable[[Tracker], list[BaseTool]], Callable[[], tuple]], ...
] = (
    (PlannerAgent, planner_tools, _planner_case),
    (ResearcherAgent, research_tools, _researcher_case),
    (FactCheckerAgent, fact_checker_tools, _fact_checker_case),
    (CriticAgent, critic_tools, _critic_case),
)

AGENT_IDS = [agent_class.name for agent_class, _, _ in TOOL_SELECTING_AGENTS]


def _agent(
    agent_class: type,
    tracker: Tracker,
    completer: ScriptedCompleter,
    tools: list[BaseTool],
):
    return agent_class(
        provider=completer,
        tracker=tracker,
        scratchpad=ScratchpadMemory(
            session_id="session-1",
            agent_name=agent_class.name,
            max_entries=20,
        ),
        tools=tools,
        config=AgentRuntimeConfig(max_iterations=2, tool_budget=2),
    )


@pytest.mark.parametrize(
    ("agent_class", "tool_factory", "case_factory"),
    TOOL_SELECTING_AGENTS,
    ids=AGENT_IDS,
)
@pytest.mark.asyncio
async def test_every_tool_selecting_agent_runs_its_loop_on_native_turns(
    agent_class: type,
    tool_factory: Callable[[Tracker], list[BaseTool]],
    case_factory: Callable[[], tuple],
    tracker: Tracker,
) -> None:
    state, decisions, outputs = case_factory()
    completer = ScriptedCompleter(decisions=decisions, outputs=outputs)
    agent = _agent(agent_class, tracker, completer, tool_factory(tracker))

    async with tracker.session_span("session-1", state.original_question):
        await agent.run(state)

    # The loop really ran, and it asked the provider for native turns.
    assert completer.react_calls
    assert completer.react_calls[0].agent_name == agent_class.name
    assert [definition.name for definition in completer.react_calls[0].tools] == list(
        agent_class.allowed_tools
    )
    # Nothing on this path ever requested the old structured decision schema.
    assert all(schema_name != "ReActDecision" for schema_name, _, _ in completer.calls)
    # And the tools were never advertised in the prompt text.
    body = completer.react_calls[0].messages[1].content
    assert "## Tools" not in body
    assert "tool_input_json" not in body


@pytest.mark.parametrize(
    ("agent_class", "tool_factory", "case_factory"),
    TOOL_SELECTING_AGENTS,
    ids=AGENT_IDS,
)
@pytest.mark.asyncio
async def test_a_native_tool_call_crosses_the_boundary_and_is_executed(
    agent_class: type,
    tool_factory: Callable[[Tracker], list[BaseTool]],
    case_factory: Callable[[], tuple],
    tracker: Tracker,
) -> None:
    """A native call reaches the loop as a real, allow-listed tool execution."""
    state, _, outputs = case_factory()
    tool_name = agent_class.allowed_tools[0]
    arguments = {
        "query_memory": '{"query": "quantum computing"}',
        "web_search": '{"query": "quantum computing"}',
    }.get(tool_name, "{}")
    completer = ScriptedCompleter(
        decisions=[
            use_tool("Look it up.", tool_name, arguments),
            finish("Enough.", "The evidence is sufficient."),
        ],
        outputs=outputs,
    )
    agent = _agent(agent_class, tracker, completer, tool_factory(tracker))

    async with tracker.session_span("session-1", state.original_question):
        outcome = await agent.run(state)

    assert outcome.react.tool_calls == 1
    tool_steps = [step for step in outcome.react.steps if step.tool_name is not None]
    assert [step.tool_name for step in tool_steps] == [tool_name]
    assert tool_steps[0].thought == "Selected tool through provider-native calling."
