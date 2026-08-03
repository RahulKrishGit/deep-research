"""Tests for the graph's node wrappers and the halt discipline."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError, PlanningError
from deep_research.graph.nodes import (
    agent_node,
    critic_node,
    refine_node,
    route_after_critic,
)
from deep_research.graph.state import (
    ROUTE_END,
    ROUTE_REFINE,
    dump_state,
    is_halted,
    load_state,
)
from deep_research.providers import ProviderConfigurationError
from deep_research.utils.types import ResearchError, ResearchState
from tests.graph_fakes import (
    FakeAgent,
    fake_critique,
    fake_finding,
    fake_research_state,
    fake_sub_topic,
    halting_error,
)


def _event_types(state: ResearchState) -> list[str]:
    return [event.event_type for event in state.events]


@pytest.mark.asyncio
async def test_a_node_merges_its_agents_update_and_brackets_it_with_events(
) -> None:
    agent = FakeAgent("planner", [{"sub_topics": [fake_sub_topic()]}])
    node = agent_node(agent)

    result = await node(dump_state(fake_research_state()))
    state = load_state(result)

    assert [topic.title for topic in state.sub_topics] == ["Error correction"]
    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
    ]
    assert state.events[-1].metadata["node"] == "planner"
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_an_agent_is_handed_state_that_already_records_its_start() -> None:
    agent = FakeAgent("researcher", [{"raw_findings": [fake_finding()]}])

    await agent_node(agent)(dump_state(fake_research_state()))

    # The agent is handed the state *after* graph.node.started is recorded,
    # so an agent that reads state.events sees its own node's start.
    assert _event_types(agent.calls[0]) == ["graph.node.started"]


@pytest.mark.asyncio
async def test_a_node_name_may_be_overridden_for_the_graph() -> None:
    agent = FakeAgent("planner")

    result = await agent_node(agent, node_name="scout")(
        dump_state(fake_research_state())
    )

    assert load_state(result).events[0].source == "graph.scout"


@pytest.mark.asyncio
async def test_a_recoverable_agent_error_stays_in_state_and_does_not_halt(
) -> None:
    recoverable = ResearchError(
        error_type="researcher_sub_topic_without_findings",
        source="agent.researcher",
        message="A high-priority sub-topic produced no findings.",
    )
    agent = FakeAgent("researcher", [{"errors": [recoverable]}])

    result = await agent_node(agent)(dump_state(fake_research_state()))
    state = load_state(result)

    assert [error.error_type for error in state.errors] == [
        "researcher_sub_topic_without_findings"
    ]
    assert not is_halted(state)
    assert state.events[-1].metadata["error_count"] == 1


@pytest.mark.asyncio
async def test_a_non_recoverable_agent_error_still_does_not_halt_the_graph(
) -> None:
    # Agents record provider outages as non-recoverable. A research pass is
    # expected to survive one; only enumerated graph errors halt.
    outage = ResearchError(
        error_type="critic_review_provider_error",
        source="agent.critic",
        message="The model provider failed while the report was reviewed.",
        recoverable=False,
    )
    agent = FakeAgent("critic", [{"errors": [outage]}])

    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    assert not is_halted(state)


@pytest.mark.parametrize(
    ("raised", "expected_type"),
    [
        (PlanningError("no plan"), "graph_planning_failed"),
        (
            AgentConfigurationError("bad scratchpad"),
            "graph_agent_configuration_error",
        ),
        (
            ProviderConfigurationError("no api key"),
            "graph_provider_configuration_error",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_configuration_failure_halts_the_run_with_state_intact(
    raised: Exception, expected_type: str
) -> None:
    agent = FakeAgent("planner", [raised])

    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    assert [error.error_type for error in state.errors] == [expected_type]
    assert state.errors[0].details == {"exception_type": type(raised).__name__}
    assert is_halted(state)
    assert state.original_question == "How mature is quantum error correction?"


@pytest.mark.asyncio
async def test_an_unexpected_agent_exception_is_not_swallowed() -> None:
    agent = FakeAgent("planner", [RuntimeError("a defect, not an outcome")])

    with pytest.raises(RuntimeError, match="a defect"):
        await agent_node(agent)(dump_state(fake_research_state()))


@pytest.mark.asyncio
async def test_an_update_the_state_model_rejects_halts_the_run() -> None:
    agent = FakeAgent("researcher", [{"iteration": 2}])

    state = load_state(await agent_node(agent)(dump_state(fake_research_state())))

    assert [error.error_type for error in state.errors] == [
        "graph_invalid_agent_state"
    ]
    assert is_halted(state)


@pytest.mark.asyncio
async def test_a_halted_run_skips_every_later_node() -> None:
    agent = FakeAgent("synthesizer", [{"report": "# never written"}])

    result = await agent_node(agent)(
        dump_state(fake_research_state(errors=[halting_error()]))
    )
    state = load_state(result)

    assert agent.calls == []
    assert state.report is None
    assert _event_types(state) == ["graph.node.skipped"]


@pytest.mark.asyncio
async def test_the_critic_node_records_the_route_it_produced() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(fake_research_state(iteration=0, max_iterations=3))
    )
    state = load_state(result)

    assert _event_types(state) == [
        "graph.node.started",
        "graph.node.completed",
        "graph.route.decided",
    ]
    assert state.events[-1].metadata == {
        "destination": ROUTE_REFINE,
        "reason": "refinement_requested",
        "iteration": 0,
        "max_iterations": 3,
        "should_continue": True,
    }
    assert route_after_critic(result) == ROUTE_REFINE


@pytest.mark.asyncio
async def test_the_critic_node_records_the_bound_overriding_the_critic() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(fake_research_state(iteration=2, max_iterations=2))
    )
    state = load_state(result)

    assert state.events[-1].metadata["reason"] == "max_iterations_reached"
    assert state.events[-1].metadata["should_continue"] is True
    assert route_after_critic(result) == ROUTE_END


@pytest.mark.asyncio
async def test_a_halted_critic_node_still_records_a_route() -> None:
    agent = FakeAgent("critic", [{"critique": fake_critique(should_continue=True)}])

    result = await critic_node(agent)(
        dump_state(fake_research_state(errors=[halting_error()]))
    )

    assert agent.calls == []
    assert load_state(result).events[-1].metadata["reason"] == "halted"
    assert route_after_critic(result) == ROUTE_END


@pytest.mark.asyncio
async def test_the_refinement_hop_advances_the_macro_iteration() -> None:
    result = await refine_node(
        dump_state(fake_research_state(iteration=0, max_iterations=3))
    )
    state = load_state(result)

    assert state.iteration == 1
    assert _event_types(state) == ["graph.refinement.started"]
    assert state.events[0].metadata == {"iteration": 1, "max_iterations": 3}


@pytest.mark.asyncio
async def test_the_refinement_hop_refuses_to_spend_a_budget_it_lacks() -> None:
    state = load_state(
        await refine_node(
            dump_state(fake_research_state(iteration=2, max_iterations=2))
        )
    )

    assert state.iteration == 2
    assert [error.error_type for error in state.errors] == [
        "graph_invalid_route"
    ]
    assert is_halted(state)


@pytest.mark.asyncio
async def test_the_refinement_hop_skips_a_halted_run() -> None:
    state = load_state(
        await refine_node(dump_state(fake_research_state(errors=[halting_error()])))
    )

    assert state.iteration == 0
    assert _event_types(state) == ["graph.node.skipped"]
