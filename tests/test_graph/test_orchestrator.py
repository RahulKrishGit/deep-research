"""Tests for the compiled research graph: sequence, loop, bound, failure."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import AgentConfigurationError
from deep_research.graph.orchestrator import (
    AGENT_NODE_ORDER,
    build_checkpointer,
    compile_research_graph,
    session_config,
)
from deep_research.graph.state import (
    CRITIC_NODE,
    NODE_NAMES,
    REFINE_NODE,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.utils.types import ResearchError, ResearchState
from tests.graph_fakes import (
    FakeAgent,
    fake_critique,
    fake_finding,
    fake_research_agents,
)


async def _run(agents, *, max_iterations: int = 3) -> ResearchState:
    graph = compile_research_graph(agents)
    channel = initial_graph_state(
        session_id="session-1",
        question="How mature is quantum error correction?",
        max_iterations=max_iterations,
    )
    result = await graph.ainvoke(
        channel, session_config("session-1", max_iterations=max_iterations)
    )
    return load_state(result)


def _nodes_visited(state: ResearchState) -> list[str]:
    return [
        event.metadata["node"]
        for event in state.events
        if event.event_type == "graph.node.started"
    ]


def _route_reasons(state: ResearchState) -> list[str]:
    return [
        event.metadata["reason"]
        for event in state.events
        if event.event_type == "graph.route.decided"
    ]


def test_the_agent_node_order_matches_the_designed_sequence() -> None:
    assert AGENT_NODE_ORDER == (
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
    )
    assert set(AGENT_NODE_ORDER) | {CRITIC_NODE, REFINE_NODE} == set(NODE_NAMES)


@pytest.mark.asyncio
async def test_the_happy_path_runs_every_agent_once_in_order() -> None:
    agents = fake_research_agents()

    state = await _run(agents)

    assert _nodes_visited(state) == [*AGENT_NODE_ORDER, CRITIC_NODE]
    assert state.report == "# Research report: pass 1"
    assert state.iteration == 0
    assert _route_reasons(state) == ["critique_satisfied"]
    assert graph_status(state) == "completed"
    assert not state.errors


@pytest.mark.asyncio
async def test_the_critic_can_send_the_graph_back_to_the_researcher() -> None:
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [
                {"raw_findings": [fake_finding("Break-even was reached.")]},
                {"raw_findings": [fake_finding("Costs fell tenfold.")]},
            ],
        ),
        synthesizer=FakeAgent(
            "synthesizer",
            [{"report": "# pass 1"}, {"report": "# pass 2"}],
        ),
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
    )

    state = await _run(agents)

    assert _nodes_visited(state) == [
        *AGENT_NODE_ORDER,
        CRITIC_NODE,
        "researcher",
        "source_evaluator",
        "fact_checker",
        "synthesizer",
        CRITIC_NODE,
    ]
    assert len(agents.planner.calls) == 1
    assert len(agents.researcher.calls) == 2
    assert _route_reasons(state) == [
        "refinement_requested",
        "critique_satisfied",
    ]
    assert graph_status(state) == "completed"


@pytest.mark.asyncio
async def test_a_refinement_pass_advances_the_macro_iteration() -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        )
    )

    state = await _run(agents)

    assert state.iteration == 1
    refinements = [
        event
        for event in state.events
        if event.event_type == "graph.refinement.started"
    ]
    assert [event.metadata["iteration"] for event in refinements] == [1]
    # The Researcher's second pass sees the critique that asked for it.
    assert agents.researcher.calls[1].critique is not None


@pytest.mark.asyncio
async def test_state_appends_accumulate_and_scalars_replace_across_passes(
) -> None:
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [
                {"raw_findings": [fake_finding("first")]},
                {"raw_findings": [fake_finding("second")]},
            ],
        ),
        synthesizer=FakeAgent(
            "synthesizer",
            [{"report": "# pass 1"}, {"report": "# pass 2"}],
        ),
        critic=FakeAgent(
            "critic",
            [
                {"critique": fake_critique(should_continue=True, score=4)},
                {"critique": fake_critique(should_continue=False, score=9)},
            ],
        ),
    )

    state = await _run(agents)

    assert [finding.content for finding in state.raw_findings] == [
        "first",
        "second",
    ]
    assert state.report == "# pass 2"
    assert state.critique is not None
    assert state.critique.score == 9
    assert len(state.evaluated_sources) == 2
    assert len(state.verified_claims) == 2


@pytest.mark.asyncio
async def test_the_iteration_bound_forces_an_end_the_critic_did_not_want(
) -> None:
    agents = fake_research_agents(
        critic=FakeAgent(
            "critic", [{"critique": fake_critique(should_continue=True, score=3)}]
        )
    )

    state = await _run(agents, max_iterations=1)

    assert len(agents.researcher.calls) == 2
    assert state.iteration == 1
    assert _route_reasons(state) == [
        "refinement_requested",
        "max_iterations_reached",
    ]
    assert graph_status(state) == "max_iterations"
    assert state.critique is not None
    assert state.critique.should_continue is True


@pytest.mark.asyncio
async def test_an_agent_failure_stops_the_run_and_keeps_what_was_collected(
) -> None:
    agents = fake_research_agents(
        fact_checker=FakeAgent(
            "fact_checker", [AgentConfigurationError("bad scratchpad")]
        )
    )

    state = await _run(agents)

    assert _nodes_visited(state) == [
        "planner",
        "researcher",
        "source_evaluator",
        "fact_checker",
    ]
    assert agents.synthesizer.calls == []
    assert agents.critic.calls == []
    assert is_halted(state)
    assert graph_status(state) == "failed"
    assert _route_reasons(state) == ["halted"]
    # Everything gathered before the failure survives.
    assert len(state.raw_findings) == 1
    assert len(state.evaluated_sources) == 1
    skipped = [
        event.metadata["node"]
        for event in state.events
        if event.event_type == "graph.node.skipped"
    ]
    assert skipped == ["synthesizer", "critic"]


@pytest.mark.asyncio
async def test_a_recoverable_agent_error_never_stops_the_graph() -> None:
    recoverable = ResearchError(
        error_type="researcher_sub_topic_without_findings",
        source="agent.researcher",
        message="A high-priority sub-topic produced no findings.",
    )
    agents = fake_research_agents(
        researcher=FakeAgent(
            "researcher",
            [{"raw_findings": [fake_finding()], "errors": [recoverable]}],
        )
    )

    state = await _run(agents)

    assert graph_status(state) == "completed"
    assert [error.error_type for error in state.errors] == [
        "researcher_sub_topic_without_findings"
    ]


@pytest.mark.asyncio
async def test_an_agent_that_returns_invalid_state_fails_the_run() -> None:
    agents = fake_research_agents(
        synthesizer=FakeAgent("synthesizer", [{"iteration": 5}])
    )

    state = await _run(agents)

    assert graph_status(state) == "failed"
    assert [error.error_type for error in state.errors] == [
        "graph_invalid_agent_state"
    ]


def test_the_session_config_pins_the_thread_and_the_superstep_bound() -> None:
    config = session_config("session-1", max_iterations=2)

    assert config["configurable"]["thread_id"] == "session-1"
    assert config["recursion_limit"] > 0


def test_a_checkpointer_is_built_only_when_it_is_asked_for() -> None:
    assert build_checkpointer(enabled=False) is None
    assert build_checkpointer(enabled=True) is not None
