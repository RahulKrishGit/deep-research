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

class FakeRun:
    def __init__(self, trace_url: str) -> None:
        self.trace_url = trace_url
        self.metadata: dict[str, Any] = {}
        self.end_calls: list[dict[str, Any]] = []

    def get_url(self) -> str:
        return self.trace_url

    def end(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.end_calls.append(
            {"outputs": outputs, "error": error, "metadata": metadata}
        )


class FakeTraceManager:
    def __init__(
        self,
        run: FakeRun,
        *,
        enter_error: Exception | None = None,
        exit_error: Exception | None = None,
    ) -> None:
        self.run = run
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.exit_calls: list[tuple[object, object, object]] = []

    async def __aenter__(self) -> FakeRun:
        if self.enter_error is not None:
            raise self.enter_error
        return self.run

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self.exit_calls.append((exc_type, exc_value, traceback))
        if self.exit_error is not None:
            raise self.exit_error


class RecordingTraceFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.managers: list[FakeTraceManager] = []

    def __call__(self, name: str, run_type: str, **kwargs: Any) -> FakeTraceManager:
        self.calls.append({"name": name, "run_type": run_type, **kwargs})
        manager = FakeTraceManager(
            FakeRun(
                "https://smith.langchain.com/o/example/r/"
                f"{len(self.calls)}"
            )
        )
        self.managers.append(manager)
        return manager


@pytest.mark.asyncio
async def test_enabled_tracing_emits_nested_runs_and_captures_trace_url() -> None:
    trace_factory = RecordingTraceFactory()
    clients: list[dict[str, Any]] = []

    def client_factory(**kwargs: Any) -> object:
        clients.append(kwargs)
        return object()

    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=client_factory,
        trace_factory=trace_factory,
    )

    async with tracker.session_span("session-1", "question") as session:
        assert session.trace_url.endswith("/1")
        async with tracker.agent_span("planner"):
            async with tracker.react_iteration_span(0):
                async with tracker.llm_span("gpt-4o", {"prompt": "plan"}) as llm:
                    llm.set_outputs({"response": "plan"})
                    llm.set_token_usage(input_tokens=12, output_tokens=4)
                async with tracker.tool_span(
                    "web_search",
                    {"query": "topic"},
                    retry_count=2,
                ) as tool:
                    tool.set_outputs({"results": 3})

    assert clients == [{"api_key": "secret-key"}]
    assert [call["run_type"] for call in trace_factory.calls] == [
        "chain",
        "chain",
        "chain",
        "llm",
        "tool",
    ]
    assert [call["name"] for call in trace_factory.calls] == [
        "research.session",
        "agent.planner",
        "react.iteration.0",
        "llm.gpt-4o",
        "tool.web_search",
    ]
    assert trace_factory.calls[0]["project_name"] == "deep-research-tests"
    assert trace_factory.calls[0]["metadata"]["session_id"] == "session-1"
    assert trace_factory.calls[3]["metadata"]["agent_name"] == "planner"
    assert trace_factory.calls[4]["metadata"]["tool_name"] == "web_search"
    assert trace_factory.managers[3].run.end_calls[-1]["metadata"][
        "token_usage"
    ] == {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}
    assert trace_factory.managers[4].run.end_calls[-1]["outputs"] == {
        "results": 3
    }
    session_metric = next(
        metric for metric in tracker.metrics if isinstance(metric, SessionMetric)
    )
    assert session_metric.trace_url.endswith("/1")


@pytest.mark.asyncio
async def test_client_creation_failure_records_error_and_runs_locally() -> None:
    def failing_client_factory(**kwargs: Any) -> object:
        raise OSError("LangSmith unavailable")

    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=failing_client_factory,
        trace_factory=ForbiddenTraceFactory(),
    )

    async with tracker.session_span("session-1", "question"):
        pass

    assert len(tracker.errors) == 1
    assert tracker.errors[0].error_type == "langsmith_tracing_failure"
    assert tracker.errors[0].details["stage"] == "client_create"
    assert isinstance(tracker.metrics[0], SessionMetric)
    assert tracker.metrics[0].success is True


@pytest.mark.asyncio
async def test_span_enter_failure_falls_back_locally_for_that_operation() -> None:
    calls = 0

    def trace_factory(name: str, run_type: str, **kwargs: Any) -> FakeTraceManager:
        nonlocal calls
        calls += 1
        return FakeTraceManager(
            FakeRun("https://smith.langchain.com/o/example/r/failure"),
            enter_error=ConnectionError("cannot create run") if calls == 1 else None,
        )

    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=trace_factory,
    )

    async with tracker.session_span("session-1", "question"):
        async with tracker.agent_span("planner"):
            pass

    assert tracker.errors[0].details["stage"] == "span_enter"
    assert [metric.success for metric in tracker.metrics] == [True, True]


@pytest.mark.asyncio
async def test_span_exit_failure_does_not_hide_operation_exception() -> None:
    manager = FakeTraceManager(
        FakeRun("https://smith.langchain.com/o/example/r/failure"),
        exit_error=ConnectionError("cannot patch run"),
    )
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=lambda *args, **kwargs: manager,
    )

    with pytest.raises(ValueError, match="research failed"):
        async with tracker.session_span("session-1", "question"):
            raise ValueError("research failed")

    assert tracker.metrics[-1].success is False
    assert tracker.metrics[-1].error_type == "ValueError"
    assert tracker.errors[-1].details["stage"] == "span_exit"


class FailingEndRun(FakeRun):
    def end(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().end(outputs=outputs, error=error, metadata=metadata)
        raise ConnectionError("cannot complete run")


@pytest.mark.asyncio
async def test_span_end_failure_does_not_hide_operation_exception() -> None:
    manager = FakeTraceManager(
        FailingEndRun("https://smith.langchain.com/o/example/r/failure")
    )
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=lambda *args, **kwargs: manager,
    )

    with pytest.raises(ValueError, match="research failed"):
        async with tracker.session_span("session-1", "question"):
            raise ValueError("research failed")

    assert tracker.metrics[-1].success is False
    assert tracker.metrics[-1].error_type == "ValueError"
    assert tracker.errors[-1].details["stage"] == "span_end"
