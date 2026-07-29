"""Tests for stable serializable observability metrics."""

import pytest
from pydantic import TypeAdapter, ValidationError

from deep_research.observability.metrics import (
    AgentMetric,
    MetricRecord,
    SessionMetric,
    TokenUsageMetric,
    ToolMetric,
)


def test_session_metric_serializes_stable_metadata() -> None:
    metric = SessionMetric(
        session_id="session-1",
        latency_ms=1250.5,
        success=True,
        trace_url="https://smith.langchain.com/o/example/r/session-run",
    )

    assert metric.model_dump() == {
        "metric_type": "session",
        "latency_ms": 1250.5,
        "success": True,
        "error_type": None,
        "session_id": "session-1",
        "trace_url": "https://smith.langchain.com/o/example/r/session-run",
    }


def test_agent_metric_distinguishes_agent_and_react_spans() -> None:
    agent = AgentMetric(
        session_id="session-1",
        agent_name="planner",
        scope="agent",
        latency_ms=80.0,
        success=True,
    )
    react = AgentMetric(
        session_id="session-1",
        agent_name="planner",
        scope="react_iteration",
        iteration=2,
        latency_ms=25.0,
        success=True,
    )

    assert agent.iteration is None
    assert react.iteration == 2


def test_react_metric_requires_iteration() -> None:
    with pytest.raises(ValidationError, match="iteration"):
        AgentMetric(
            session_id="session-1",
            agent_name="planner",
            scope="react_iteration",
            latency_ms=25.0,
            success=True,
        )


def test_token_usage_requires_consistent_total() -> None:
    with pytest.raises(ValidationError, match="total_tokens"):
        TokenUsageMetric(
            session_id="session-1",
            agent_name="planner",
            model="gpt-4o",
            input_tokens=10,
            output_tokens=5,
            total_tokens=99,
            latency_ms=40.0,
            success=True,
        )


def test_metric_union_round_trips_each_record() -> None:
    metrics: list[MetricRecord] = [
        ToolMetric(
            session_id="session-1",
            agent_name="researcher",
            tool_name="web_search",
            iteration=1,
            latency_ms=75.0,
            success=False,
            error_type="TimeoutError",
            retry_count=2,
        ),
        TokenUsageMetric(
            session_id="session-1",
            agent_name="planner",
            model="gpt-4o",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            latency_ms=40.0,
            success=True,
        ),
    ]
    adapter = TypeAdapter(list[MetricRecord])

    restored = adapter.validate_json(adapter.dump_json(metrics))

    assert restored == metrics


@pytest.mark.parametrize(
    "kwargs",
    [
        {"latency_ms": -0.1, "success": True, "error_type": None},
        {"latency_ms": 1.0, "success": True, "error_type": "ValueError"},
        {"latency_ms": 1.0, "success": False, "error_type": None},
    ],
)
def test_outcome_metrics_reject_invalid_failure_metadata(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SessionMetric(session_id="session-1", **kwargs)
