"""Tests for local and LangSmith-backed observability spans."""

from __future__ import annotations

from typing import Any

import pytest

from deep_research.observability.context import (
    LangSmithRuntimeConfig,
    current_trace_context,
)
from deep_research.observability.metrics import (
    AgentMetric,
    SessionMetric,
    TokenUsageMetric,
    ToolMetric,
)
from deep_research.observability.tracker import Tracker


class ForbiddenClientFactory:
    def __call__(self, **kwargs: Any) -> object:
        raise AssertionError("disabled tracing must not create a LangSmith client")


class ForbiddenTraceFactory:
    def __call__(self, *args: Any, **kwargs: Any) -> object:
        raise AssertionError("disabled tracing must not open a LangSmith trace")


@pytest.mark.asyncio
async def test_disabled_nested_spans_record_local_context_events_and_metrics() -> None:
    tracker = Tracker(
        LangSmithRuntimeConfig(tracing_enabled=False),
        client_factory=ForbiddenClientFactory(),
        trace_factory=ForbiddenTraceFactory(),
    )

    async with tracker.session_span("session-1", "Why is the sky blue?") as session:
        assert session.trace_url is None
        assert current_trace_context() is not None
        assert current_trace_context().session_id == "session-1"

        async with tracker.agent_span("planner"):
            assert current_trace_context().agent_name == "planner"

            async with tracker.react_iteration_span(2):
                assert current_trace_context().iteration == 2

                async with tracker.llm_span("gpt-4o", {"prompt": "plan"}) as llm:
                    llm.set_token_usage(input_tokens=11, output_tokens=7)
                    assert current_trace_context().model == "gpt-4o"

                llm_parent_context = current_trace_context()
                assert llm_parent_context is not None
                assert llm_parent_context.agent_name == "planner"
                assert llm_parent_context.iteration == 2
                assert llm_parent_context.model is None
                assert llm_parent_context.tool_name is None

                async with tracker.tool_span(
                    "web_search",
                    {"query": "Rayleigh scattering"},
                    retry_count=1,
                ):
                    assert current_trace_context().tool_name == "web_search"

                tool_parent_context = current_trace_context()
                assert tool_parent_context is not None
                assert tool_parent_context.agent_name == "planner"
                assert tool_parent_context.iteration == 2
                assert tool_parent_context.model is None
                assert tool_parent_context.tool_name is None

            react_parent_context = current_trace_context()
            assert react_parent_context is not None
            assert react_parent_context.agent_name == "planner"
            assert react_parent_context.iteration is None
            assert react_parent_context.model is None
            assert react_parent_context.tool_name is None

        agent_parent_context = current_trace_context()
        assert agent_parent_context is not None
        assert agent_parent_context.session_id == "session-1"
        assert agent_parent_context.agent_name is None
        assert agent_parent_context.iteration is None
        assert agent_parent_context.model is None
        assert agent_parent_context.tool_name is None

    assert current_trace_context() is None
    assert tracker.events[4].metadata["token_usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    assert [event.event_type for event in tracker.events] == [
        "observability.span.started",
        "observability.span.started",
        "observability.span.started",
        "observability.span.started",
        "observability.span.completed",
        "observability.span.started",
        "observability.span.completed",
        "observability.span.completed",
        "observability.span.completed",
        "observability.span.completed",
    ]
    assert tracker.errors == ()
    assert any(isinstance(metric, SessionMetric) for metric in tracker.metrics)
    assert any(
        isinstance(metric, AgentMetric) and metric.scope == "react_iteration"
        for metric in tracker.metrics
    )
    assert any(isinstance(metric, ToolMetric) for metric in tracker.metrics)
    assert any(
        isinstance(metric, TokenUsageMetric) and metric.total_tokens == 18
        for metric in tracker.metrics
    )


@pytest.mark.asyncio
async def test_operation_failure_is_recorded_and_propagated() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))

    with pytest.raises(RuntimeError, match="provider failed"):
        async with tracker.session_span("session-1", "question"):
            async with tracker.llm_span("gpt-4o", {"prompt": "question"}):
                raise RuntimeError("provider failed")

    assert isinstance(tracker.metrics[0], TokenUsageMetric)
    assert tracker.metrics[0].success is False
    assert tracker.metrics[0].error_type == "RuntimeError"
    assert tracker.metrics[-1].success is False
    assert tracker.metrics[-1].error_type == "RuntimeError"
    assert tracker.errors == ()
    assert tracker.events[-1].metadata["success"] is False
    assert tracker.events[-1].metadata["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_child_span_requires_an_active_session() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))

    with pytest.raises(RuntimeError, match="active session span"):
        async with tracker.agent_span("planner"):
            pass