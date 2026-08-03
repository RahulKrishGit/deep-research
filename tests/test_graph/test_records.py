"""Tests for graph-level event and error records."""

from __future__ import annotations

import pytest

from deep_research.graph.errors import (
    GRAPH_ERROR_REASONS,
    GraphConfigurationError,
    GraphError,
    GraphResumeError,
    agent_configuration_error,
    graph_error,
    invalid_agent_state_error,
    invalid_route_error,
    planning_failed_error,
    provider_configuration_error,
)
from deep_research.graph.events import (
    graph_event,
    node_completed_event,
    node_skipped_event,
    node_started_event,
    refinement_started_event,
    route_decided_event,
    session_completed_event,
    session_started_event,
)
from deep_research.graph.state import (
    GRAPH_ROUTES,
    GRAPH_STATUSES,
    HALTING_ERROR_TYPES,
    ROUTE_REFINE,
)


def test_every_halting_error_type_has_an_enumerated_reason() -> None:
    assert HALTING_ERROR_TYPES <= set(GRAPH_ERROR_REASONS)
    for reason in GRAPH_ERROR_REASONS.values():
        assert reason.strip()


def test_an_unenumerated_error_type_cannot_be_recorded() -> None:
    with pytest.raises(ValueError, match="unknown graph error type"):
        graph_error(error_type="graph_something_new")


def test_a_graph_error_names_its_node_in_the_source() -> None:
    recorded = graph_error(
        error_type="graph_invalid_agent_state",
        node="researcher",
        details={"exception_type": "ValidationError"},
    )

    assert recorded.source == "graph.researcher"
    assert recorded.error_type == "graph_invalid_agent_state"
    assert recorded.message == GRAPH_ERROR_REASONS["graph_invalid_agent_state"]
    assert recorded.recoverable is False
    assert recorded.details == {"exception_type": "ValidationError"}


def test_a_node_free_graph_error_is_attributed_to_the_graph() -> None:
    assert graph_error(error_type="graph_planning_failed").source == "graph"


def test_a_blank_node_is_attributed_to_the_graph() -> None:
    assert (
        graph_error(error_type="graph_planning_failed", node="   ").source
        == "graph"
    )


@pytest.mark.parametrize(
    ("builder", "expected_type"),
    [
        (agent_configuration_error, "graph_agent_configuration_error"),
        (planning_failed_error, "graph_planning_failed"),
        (provider_configuration_error, "graph_provider_configuration_error"),
        (invalid_agent_state_error, "graph_invalid_agent_state"),
    ],
)
def test_failure_builders_record_the_exception_type_only(
    builder: object, expected_type: str
) -> None:
    recorded = builder(ValueError("sk-secret-leaked-into-the-message"), node="planner")

    assert recorded.error_type == expected_type
    assert recorded.details == {"exception_type": "ValueError"}
    assert "secret" not in recorded.message
    assert recorded.recoverable is False


def test_an_invalid_route_records_the_budget_it_violated() -> None:
    recorded = invalid_route_error(node="refine", iteration=3, max_iterations=3)

    assert recorded.error_type == "graph_invalid_route"
    assert recorded.details == {"iteration": 3, "max_iterations": 3}
    assert recorded.recoverable is False


def test_graph_exceptions_share_one_base() -> None:
    assert issubclass(GraphConfigurationError, GraphError)
    assert issubclass(GraphResumeError, GraphError)


def test_a_graph_event_names_its_node_in_the_source() -> None:
    event = graph_event(
        event_type="graph.node.started",
        message="Node started.",
        node="planner",
        metadata={"iteration": 0},
    )

    assert event.source == "graph.planner"
    assert event.metadata == {"iteration": 0}


def test_a_blank_event_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="event_type"):
        graph_event(event_type="  ", message="Node started.")


def test_a_blank_event_node_is_attributed_to_the_graph() -> None:
    event = graph_event(
        event_type="graph.node.started",
        message="Node started.",
        node="   ",
    )

    assert event.source == "graph"


def test_node_lifecycle_events_carry_their_counts() -> None:
    started = node_started_event("researcher", iteration=1)
    completed = node_completed_event(
        "researcher", iteration=1, event_count=4, error_count=1
    )
    skipped = node_skipped_event("synthesizer", iteration=1)

    assert started.event_type == "graph.node.started"
    assert started.metadata == {"node": "researcher", "iteration": 1}
    assert completed.event_type == "graph.node.completed"
    assert completed.metadata["event_count"] == 4
    assert completed.metadata["error_count"] == 1
    assert skipped.event_type == "graph.node.skipped"
    assert skipped.metadata["reason"] == "halted"


def test_a_route_decision_records_an_enumerated_reason() -> None:
    event = route_decided_event(
        destination=ROUTE_REFINE,
        reason="refinement_requested",
        iteration=0,
        max_iterations=3,
        should_continue=True,
    )

    assert event.event_type == "graph.route.decided"
    assert event.metadata["destination"] == ROUTE_REFINE
    assert event.metadata["reason"] == "refinement_requested"
    assert event.metadata["should_continue"] is True
    assert GRAPH_ROUTES["refinement_requested"] in event.message


def test_a_route_decision_refuses_an_unenumerated_reason() -> None:
    with pytest.raises(ValueError, match="unknown route reason"):
        route_decided_event(
            destination=ROUTE_REFINE,
            reason="because",
            iteration=0,
            max_iterations=3,
            should_continue=True,
        )


def test_a_refinement_announces_the_iteration_it_opened() -> None:
    event = refinement_started_event(iteration=1, max_iterations=3)

    assert event.event_type == "graph.refinement.started"
    assert event.metadata == {"iteration": 1, "max_iterations": 3}


def test_session_events_bracket_the_run() -> None:
    started = session_started_event(
        session_id="session-1", max_iterations=3, checkpointing=True
    )
    completed = session_completed_event(
        status="completed", iteration=1, error_count=0, has_report=True
    )

    assert started.event_type == "graph.session.started"
    assert started.metadata["checkpointing"] is True
    assert completed.event_type == "graph.session.completed"
    assert completed.metadata["status"] in GRAPH_STATUSES
    assert completed.metadata["has_report"] is True


def test_a_session_completion_refuses_an_unenumerated_status() -> None:
    with pytest.raises(ValueError, match="unknown graph status"):
        session_completed_event(
            status="finished", iteration=0, error_count=0, has_report=False
        )
