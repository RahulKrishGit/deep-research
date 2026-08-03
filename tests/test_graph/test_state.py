"""Tests for the graph channel, the halt discipline, and routing."""

from __future__ import annotations

import pytest

from deep_research.graph.state import (
    DEFAULT_MAX_ITERATIONS,
    GRAPH_ROUTES,
    GRAPH_STATUSES,
    HALTING_ERROR_TYPES,
    NODE_NAMES,
    ROUTE_END,
    ROUTE_REFINE,
    dump_state,
    graph_recursion_limit,
    graph_route,
    graph_status,
    initial_graph_state,
    is_halted,
    load_state,
)
from deep_research.utils.types import MemorySnapshot, ResearchError, SubTopic
from tests.graph_fakes import fake_critique, fake_research_state, halting_error


def test_the_initial_channel_carries_the_question_and_the_budget() -> None:
    channel = initial_graph_state(
        session_id="session-1",
        question="  How mature is quantum error correction?  ",
        max_iterations=2,
        memory_context=MemorySnapshot(suggested_strategies=["start broad"]),
    )
    state = load_state(channel)

    assert set(channel) == {"state"}
    assert state.session_id == "session-1"
    assert state.original_question == "How mature is quantum error correction?"
    assert state.max_iterations == 2
    assert state.iteration == 0
    assert state.memory_context.suggested_strategies == ["start broad"]


def test_the_initial_channel_defaults_to_an_empty_memory_snapshot() -> None:
    state = load_state(
        initial_graph_state(session_id="session-1", question="Why?")
    )

    assert state.memory_context == MemorySnapshot()
    assert state.max_iterations == DEFAULT_MAX_ITERATIONS


def test_the_channel_round_trips_a_populated_state_as_plain_json() -> None:
    state = fake_research_state(
        sub_topics=[
            SubTopic(
                title="Error correction",
                rationale="It is the bottleneck.",
                search_queries=["qec 2025"],
                success_criteria=["a logical error rate is quoted"],
                priority=1,
            )
        ],
        report="# Research report",
        critique=fake_critique(should_continue=False, score=8),
    )

    channel = dump_state(state)

    assert isinstance(channel["state"], dict)
    assert isinstance(channel["state"]["sub_topics"], list)
    assert isinstance(channel["state"]["sub_topics"][0], dict)
    assert load_state(channel) == state


def test_only_enumerated_error_types_halt_a_run() -> None:
    survivable = ResearchError(
        error_type="critic_review_provider_error",
        source="agent.critic",
        message="The model provider failed while the report was reviewed.",
        recoverable=False,
    )

    assert not is_halted(fake_research_state())
    assert not is_halted(fake_research_state(errors=[survivable]))
    assert is_halted(fake_research_state(errors=[halting_error()]))


def test_a_halted_run_ends_whatever_the_critic_recommended() -> None:
    state = fake_research_state(
        errors=[halting_error()],
        critique=fake_critique(should_continue=True),
    )

    assert graph_route(state) == (ROUTE_END, "halted")
    assert graph_status(state) == "failed"


def test_a_run_with_no_critique_ends_as_incomplete() -> None:
    assert graph_route(fake_research_state()) == (ROUTE_END, "missing_critique")
    assert graph_status(fake_research_state()) == "incomplete"


def test_a_satisfied_critic_ends_the_run() -> None:
    state = fake_research_state(critique=fake_critique(should_continue=False, score=9))

    assert graph_route(state) == (ROUTE_END, "critique_satisfied")
    assert graph_status(state) == "completed"


def test_an_unsatisfied_critic_buys_a_refinement_while_budget_remains() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=True),
        iteration=1,
        max_iterations=3,
    )

    assert graph_route(state) == (ROUTE_REFINE, "refinement_requested")


def test_the_iteration_bound_beats_the_critics_recommendation() -> None:
    state = fake_research_state(
        critique=fake_critique(should_continue=True),
        iteration=2,
        max_iterations=2,
    )

    assert graph_route(state) == (ROUTE_END, "max_iterations_reached")
    assert graph_status(state) == "max_iterations"


def test_every_routing_reason_is_enumerated_and_maps_to_a_status() -> None:
    reasons = {
        graph_route(state)[1]
        for state in (
            fake_research_state(errors=[halting_error()]),
            fake_research_state(),
            fake_research_state(critique=fake_critique(should_continue=False)),
            fake_research_state(
                critique=fake_critique(should_continue=True),
                max_iterations=2,
            ),
            fake_research_state(
                critique=fake_critique(should_continue=True),
                iteration=1,
                max_iterations=1,
            ),
        )
    }

    assert reasons == set(GRAPH_ROUTES)
    for reason in GRAPH_ROUTES:
        assert GRAPH_ROUTES[reason].strip()


def test_the_status_vocabulary_is_closed() -> None:
    assert set(GRAPH_STATUSES) == {
        "completed",
        "max_iterations",
        "incomplete",
        "failed",
    }


def test_the_recursion_limit_covers_every_planned_pass() -> None:
    assert graph_recursion_limit(1) == 2 * len(NODE_NAMES) + 10
    assert graph_recursion_limit(3) > graph_recursion_limit(1)
    with pytest.raises(ValueError, match="max_iterations"):
        graph_recursion_limit(0)


def test_the_node_names_are_unique_and_ordered() -> None:
    assert len(set(NODE_NAMES)) == len(NODE_NAMES)
    assert NODE_NAMES[0] == "planner"
    assert NODE_NAMES[-1] == "refine"


def test_the_halting_error_types_are_all_graph_owned() -> None:
    assert HALTING_ERROR_TYPES
    assert all(name.startswith("graph_") for name in HALTING_ERROR_TYPES)
