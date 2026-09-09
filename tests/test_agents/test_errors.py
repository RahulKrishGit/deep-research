"""Tests for agent exception contracts and structured error recording."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.agents.errors import (
    AgentConfigurationError,
    AgentError,
    agent_error,
    agent_provider_failure_details,
)
from deep_research.observability import TokenUsage
from deep_research.providers import (
    ProviderError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
    provider_failure_snapshot,
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


def test_provider_failure_snapshot_projects_each_finite_kind() -> None:
    telemetry = ProviderResponseTelemetry(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=8, output_tokens=4096),
        request_attempt=2,
        structured_attempt=1,
    )
    diagnostics = (
        StructuredValidationDiagnostic(
            attempt=2,
            field_paths=("sub_topics.0.title",),
            category="schema_output",
        ),
        StructuredValidationDiagnostic(
            attempt=3,
            field_paths=("bad path",),
            category="schema_output",
        ),
    )
    cases = (
        (ProviderOutputLimitError(telemetry), "output_limit"),
        (StructuredOutputError("invalid", diagnostics=diagnostics), "schema_output"),
        (ProviderTimeoutError("timeout"), "provider_timeout"),
        (ProviderRateLimitError("rate limit"), "provider_rate_limit"),
        (
            ProviderResponseError(
                "transport", retryable=True, failure_category="transport"
            ),
            "provider_transport",
        ),
        (
            ProviderResponseError(
                "service unavailable",
                retryable=True,
                failure_category="http",
                http_status_code=503,
            ),
            "provider_http",
        ),
        (
            ProviderResponseError(
                "unauthorized", failure_category="http", http_status_code=401
            ),
            "provider_http",
        ),
        (ProviderResponseError("response"), "provider_response"),
        (ProviderError("failure"), "provider_failure"),
    )

    snapshots = [provider_failure_snapshot(error) for error, _ in cases]

    assert [snapshot.kind for snapshot in snapshots] == [
        expected_kind for _, expected_kind in cases
    ]
    output_limit = snapshots[0]
    assert output_limit.configured_max_tokens == 4096
    assert output_limit.usage == TokenUsage(
        input_tokens=8, output_tokens=4096, total_tokens=4104
    )
    assert output_limit.request_attempt == 2
    assert output_limit.structured_attempt == 1
    schema_output = snapshots[1]
    assert schema_output.diagnostics == (
        diagnostics[0],
        StructuredValidationDiagnostic(
            attempt=3, field_paths=("$",), category="schema_output"
        ),
    )
    assert snapshots[5].retryable is True
    assert snapshots[5].http_status_code == 503
    assert snapshots[6].retryable is False
    assert snapshots[6].http_status_code == 401


def test_provider_failure_snapshot_is_immutable_and_redacts_error_messages() -> None:
    error = ProviderTimeoutError("PROVIDER_SECRET_SENTINEL")

    snapshot = provider_failure_snapshot(error)
    serialized = snapshot.model_dump(mode="json")

    assert "PROVIDER_SECRET_SENTINEL" not in serialized
    assert "PROVIDER_SECRET_SENTINEL" not in repr(serialized)
    with pytest.raises(ValidationError):
        snapshot.kind = "provider_response"


def test_agent_provider_failure_details_returns_json_safe_snapshot() -> None:
    details = agent_provider_failure_details(
        "  research  ",
        ProviderRateLimitError("PROVIDER_SECRET_SENTINEL"),
        iteration=2,
    )

    assert details == {
        "operation": "research",
        "provider_failure": {
            "kind": "provider_rate_limit",
            "exception_type": "ProviderRateLimitError",
            "retryable": True,
            "http_status_code": None,
            "configured_max_tokens": None,
            "usage": None,
            "request_attempt": None,
            "structured_attempt": None,
            "diagnostics": [],
        },
        "iteration": 2,
    }
    assert "exception_type" not in details


def test_agent_provider_failure_details_rejects_blank_operation() -> None:
    with pytest.raises(ValueError, match="operation must not be blank"):
        agent_provider_failure_details("   ", ProviderTimeoutError("timeout"))


@pytest.mark.parametrize(
    "reserved_key", ["provider_failure", "exception_type"]
)
def test_agent_provider_failure_details_rejects_reserved_extra_keys(
    reserved_key: str,
) -> None:
    with pytest.raises(ValueError, match="reserved"):
        agent_provider_failure_details(
            "research",
            ProviderTimeoutError("opaque provider failure"),
            **{reserved_key: "override"},
        )


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
