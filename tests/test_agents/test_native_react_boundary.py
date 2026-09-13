"""The one native tool boundary every model-directed agent crosses.

A regression guard, not a unit test of one agent: the four agents that ask a
model to select a tool must all reach the provider through
``BaseAgent._complete_react_decision`` and ``complete_react``. The moment one of
them goes back to requesting ``ReActDecision`` through ``complete_structured``,
``ScriptedCompleter`` raises and this file fails.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from deep_research.agents.critic import CriticAgent
from deep_research.agents.fact_checker import FactCheckerAgent
from deep_research.agents.planner import PlannerAgent
from deep_research.agents.prompts import AgentTask
from deep_research.agents.researcher import ResearcherAgent
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.observability import Tracker
from deep_research.tools.base import BaseTool
from deep_research.utils.config import AgentRuntimeConfig
from tests.agent_fakes import ScriptedCompleter, finish, use_tool
from tests.research_fakes import (
    critic_tools,
    fact_checker_tools,
    planner_tools,
    research_tools,
)

TOOL_SELECTING_AGENTS: tuple[tuple[type, Callable[[Tracker], list[BaseTool]]], ...] = (
    (PlannerAgent, planner_tools),
    (ResearcherAgent, research_tools),
    (FactCheckerAgent, fact_checker_tools),
    (CriticAgent, critic_tools),
)


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
    ("agent_class", "tool_factory"),
    TOOL_SELECTING_AGENTS,
    ids=[agent_class.name for agent_class, _ in TOOL_SELECTING_AGENTS],
)
@pytest.mark.asyncio
async def test_every_tool_selecting_agent_uses_the_native_boundary(
    agent_class: type,
    tool_factory: Callable[[Tracker], list[BaseTool]],
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[use_tool("Look it up.", "web_search", '{"query": "qec"}')]
    )
    agent = _agent(agent_class, tracker, completer, tool_factory(tracker))

    async with tracker.session_span("session-1", "question"):
        decision = await agent._complete_react_decision(
            AgentTask(instruction="Look something up."),
            iteration=1,
        )

    assert decision.action == "use_tool"
    assert decision.tool_name == "web_search"
    assert completer.react_calls
    assert completer.react_calls[0].agent_name == agent_class.name
    # The tools travel on the request, and no call went through the old path.
    assert [definition.name for definition in completer.react_calls[0].tools] == list(
        agent_class.allowed_tools
    )
    assert all(
        schema_name != "ReActDecision"
        for schema_name, _, _ in completer.calls
    )


@pytest.mark.parametrize(
    ("agent_class", "tool_factory"),
    TOOL_SELECTING_AGENTS,
    ids=[agent_class.name for agent_class, _ in TOOL_SELECTING_AGENTS],
)
@pytest.mark.asyncio
async def test_a_native_final_answer_still_ends_the_loop(
    agent_class: type,
    tool_factory: Callable[[Tracker], list[BaseTool]],
    tracker: Tracker,
) -> None:
    completer = ScriptedCompleter(
        decisions=[finish("Nothing to check.", "The evidence is sufficient.")]
    )
    agent = _agent(agent_class, tracker, completer, tool_factory(tracker))

    async with tracker.session_span("session-1", "question"):
        decision = await agent._complete_react_decision(
            AgentTask(instruction="Look something up."),
            iteration=1,
        )

    assert decision.action == "finish"
    assert decision.final_answer == "The evidence is sufficient."
    assert decision.tool_input_json == "{}"
