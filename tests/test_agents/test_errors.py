"""Tests for agent exception contracts and structured error recording."""

from __future__ import annotations

import pytest

from deep_research.agents.errors import (
    AgentConfigurationError,
    AgentError,
    agent_error,
)


def test_configuration_error_is_an_agent_error() -> None:
    assert issubclass(AgentConfigurationError, AgentError)
    assert issubclass(AgentError, Exception)


def test_agent_error_namespaces_the_source_by_agent_name() -> None:
    recorded = agent_error(
        agent_name="researcher",
        error_type="agent_unknown_tool",
        message="hallucinated_tool is not available to this agent.",
    )

    assert recorded.source == "agent.researcher"
    assert recorded.error_type == "agent_unknown_tool"
    assert recorded.recoverable is True
    assert recorded.details == {}


def test_agent_error_records_non_recoverable_failures_with_details() -> None:
    recorded = agent_error(
        agent_name="planner",
        error_type="agent_provider_error",
        message="The provider failed and the loop stopped.",
        recoverable=False,
        details={"iteration": 2, "exception_type": "ProviderTimeoutError"},
    )

    assert recorded.recoverable is False
    assert recorded.details == {
        "iteration": 2,
        "exception_type": "ProviderTimeoutError",
    }


def test_agent_error_rejects_a_blank_agent_name() -> None:
    with pytest.raises(ValueError, match="agent_name must not be blank"):
        agent_error(agent_name="   ", error_type="x", message="y")


def test_agent_error_copies_its_details_mapping() -> None:
    details: dict[str, int] = {"iteration": 1}

    recorded = agent_error(
        agent_name="researcher",
        error_type="agent_tool_failed",
        message="web_search failed.",
        details=details,
    )
    details["iteration"] = 99

    assert recorded.details == {"iteration": 1}


def test_planning_error_is_an_agent_error_carrying_its_problems() -> None:
    from deep_research.agents.errors import PlanningError

    error = PlanningError(
        "The planner could not produce a valid research plan.",
        problems=["the plan has 1 valid sub-topics; produce between 3 and 7"],
    )

    assert isinstance(error, AgentError)
    assert error.problems == (
        "the plan has 1 valid sub-topics; produce between 3 and 7",
    )
    assert str(error) == "The planner could not produce a valid research plan."


def test_planning_error_defaults_to_no_problems() -> None:
    from deep_research.agents.errors import PlanningError

    assert PlanningError("no plan").problems == ()
