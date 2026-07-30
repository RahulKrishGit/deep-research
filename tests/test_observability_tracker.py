"""Tests for local and LangSmith-backed observability spans."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

import deep_research.observability.tracker as tracker_module
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
        exit_error: BaseException | None = None,
        on_enter: Callable[[FakeRun], None] | None = None,
        on_exit: Callable[[FakeRun], None] | None = None,
    ) -> None:
        self.run = run
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.on_enter = on_enter
        self.on_exit = on_exit
        self.exit_calls: list[tuple[object, object, object]] = []

    async def __aenter__(self) -> FakeRun:
        if self.enter_error is not None:
            raise self.enter_error
        if self.on_enter is not None:
            self.on_enter(self.run)
        return self.run

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self.exit_calls.append((exc_type, exc_value, traceback))
        if self.on_exit is not None:
            self.on_exit(self.run)
        if self.exit_error is not None:
            raise self.exit_error


class RecordingTraceFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.managers: list[FakeTraceManager] = []

    def __call__(self, name: str, run_type: str, **kwargs: Any) -> FakeTraceManager:
        self.calls.append({"name": name, "run_type": run_type, **kwargs})
        manager = FakeTraceManager(
            FakeRun(f"https://smith.langchain.com/o/example/r/{len(self.calls)}")
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

    assert clients[0]["api_key"] == "secret-key"
    assert callable(clients[0]["tracing_error_callback"])
    assert callable(clients[0]["anonymizer"])
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
    assert trace_factory.managers[3].run.end_calls[-1]["metadata"]["token_usage"] == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
    }
    assert trace_factory.managers[4].run.end_calls[-1]["outputs"] == {"results": 3}
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


class CancelledEndRun(FakeRun):
    def end(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().end(outputs=outputs, error=error, metadata=metadata)
        raise asyncio.CancelledError("cleanup cancelled")


def _cancelled_cleanup_manager(stage: str) -> FakeTraceManager:
    trace_url = "https://smith.langchain.com/o/example/r/cancelled"
    if stage == "span_end":
        return FakeTraceManager(CancelledEndRun(trace_url))
    return FakeTraceManager(
        FakeRun(trace_url),
        exit_error=asyncio.CancelledError("cleanup cancelled"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["span_end", "span_exit"])
async def test_cleanup_cancellation_does_not_replace_active_research_exception(
    stage: str,
) -> None:
    manager = _cancelled_cleanup_manager(stage)
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

    assert tracker.errors[-1].details["stage"] == stage
    assert tracker.metrics[-1].success is False
    assert tracker.events[-1].event_type == "observability.span.completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["span_end", "span_exit"])
async def test_cleanup_cancellation_propagates_without_active_research_exception(
    stage: str,
) -> None:
    manager = _cancelled_cleanup_manager(stage)
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=lambda *args, **kwargs: manager,
    )

    with pytest.raises(asyncio.CancelledError, match="cleanup cancelled"):
        async with tracker.session_span("session-1", "question"):
            pass


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


class RecordingTracingContext:
    def __init__(
        self,
        factory: "RecordingTracingContextFactory",
        kwargs: dict[str, Any],
    ) -> None:
        self.factory = factory
        self.kwargs = kwargs
        self.exit_calls: list[tuple[object, object, object]] = []

    def __enter__(self) -> None:
        if self.factory.enter_error is not None:
            raise self.factory.enter_error
        self.factory.active_depth += 1

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.exit_calls.append((exc_type, exc_value, traceback))
        self.factory.active_depth -= 1
        if self.factory.exit_error is not None:
            raise self.factory.exit_error


class RecordingTracingContextFactory:
    def __init__(
        self,
        *,
        enter_error: Exception | None = None,
        exit_error: Exception | None = None,
    ) -> None:
        self.enter_error = enter_error
        self.exit_error = exit_error
        self.active_depth = 0
        self.contexts: list[RecordingTracingContext] = []

    def __call__(self, **kwargs: Any) -> RecordingTracingContext:
        context = RecordingTracingContext(self, kwargs)
        self.contexts.append(context)
        return context


class ContextAwareTraceFactory:
    def __init__(self, contexts: RecordingTracingContextFactory) -> None:
        self.contexts = contexts
        self.calls: list[dict[str, Any]] = []
        self.managers: list[FakeTraceManager] = []
        self.active_runs: list[FakeRun] = []

    def __call__(self, name: str, run_type: str, **kwargs: Any) -> FakeTraceManager:
        parent = self.active_runs[-1] if self.active_runs else None
        self.calls.append(
            {
                "name": name,
                "run_type": run_type,
                "parent": parent,
                "context_depth": self.contexts.active_depth,
                **kwargs,
            }
        )
        manager = FakeTraceManager(
            FakeRun(f"https://smith.langchain.com/o/example/r/{len(self.calls)}"),
            on_enter=self.active_runs.append,
            on_exit=lambda run: self.active_runs.pop(),
        )
        self.managers.append(manager)
        return manager


@pytest.mark.asyncio
async def test_enabled_tracing_activates_context_nests_runs_and_inherits_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = RecordingTracingContextFactory()
    trace_factory = ContextAwareTraceFactory(contexts)
    client = object()
    monkeypatch.setattr(
        tracker_module,
        "tracing_context",
        contexts,
        raising=False,
    )
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: client,
        trace_factory=trace_factory,
    )

    async with tracker.session_span("session-1", "question") as session:
        session_url = session.trace_url
        async with tracker.agent_span("planner") as agent:
            assert agent.trace_url == session_url
            async with tracker.react_iteration_span(0) as iteration:
                assert iteration.trace_url == session_url
                async with tracker.llm_span("gpt-4o", {"prompt": "plan"}) as llm:
                    assert llm.trace_url == session_url
                    llm.set_outputs({"response": "plan"})
                    llm.set_token_usage(input_tokens=12, output_tokens=4)
                async with tracker.tool_span("web_search", {"query": "topic"}) as tool:
                    assert tool.trace_url == session_url
                    tool.set_outputs({"results": 3})

    assert session_url == "https://smith.langchain.com/o/example/r/1"
    assert [call["context_depth"] for call in trace_factory.calls] == [1, 2, 3, 4, 4]
    assert [context.kwargs for context in contexts.contexts] == [
        {
            "enabled": True,
            "project_name": "deep-research-tests",
            "client": client,
        }
    ] * 5
    assert [call["parent"] for call in trace_factory.calls] == [
        None,
        trace_factory.managers[0].run,
        trace_factory.managers[1].run,
        trace_factory.managers[2].run,
        trace_factory.managers[2].run,
    ]
    assert contexts.active_depth == 0
    assert trace_factory.active_runs == []

    llm_metadata = trace_factory.managers[3].run.end_calls[-1]["metadata"]
    assert llm_metadata["success"] is True
    assert "latency_ms" in llm_metadata
    assert llm_metadata["trace_url"] == session_url
    assert llm_metadata["model"] == "gpt-4o"
    assert llm_metadata["token_usage"] == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
    }
    tool_metadata = trace_factory.managers[4].run.end_calls[-1]["metadata"]
    assert tool_metadata["success"] is True
    assert "latency_ms" in tool_metadata
    assert tool_metadata["trace_url"] == session_url


class InvalidUrlRun(FakeRun):
    def __init__(self, value: Any) -> None:
        super().__init__("https://smith.langchain.com/o/example/r/invalid")
        self.value = value

    def get_url(self) -> str:
        return self.value


class RaisingUrlRun(FakeRun):
    def get_url(self) -> str:
        raise ConnectionError("cannot retrieve trace URL")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "  ", None, 42])
async def test_invalid_session_trace_url_falls_back_locally(value: Any) -> None:
    manager = FakeTraceManager(InvalidUrlRun(value))
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=lambda *args, **kwargs: manager,
    )

    async with tracker.session_span("session-1", "question") as session:
        assert session.trace_url is None

    assert tracker.errors[-1].details["stage"] == "trace_url"
    assert "trace_url" not in manager.run.end_calls[-1]["metadata"]


@pytest.mark.asyncio
async def test_raising_session_trace_url_falls_back_locally() -> None:
    manager = FakeTraceManager(RaisingUrlRun("ignored"))
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=lambda *args, **kwargs: manager,
    )

    async with tracker.session_span("session-1", "question") as session:
        assert session.trace_url is None

    assert tracker.errors[-1].details["stage"] == "trace_url"


@pytest.mark.asyncio
async def test_successful_operation_survives_remote_end_and_exit_failures() -> None:
    manager = FakeTraceManager(
        FailingEndRun("https://smith.langchain.com/o/example/r/failure"),
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

    async with tracker.session_span("session-1", "question"):
        pass

    assert tracker.metrics[-1].success is True
    assert [error.details["stage"] for error in tracker.errors] == [
        "span_end",
        "span_exit",
    ]


@pytest.mark.asyncio
async def test_tracing_context_entry_failure_falls_back_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = RecordingTracingContextFactory(
        enter_error=ConnectionError("context unavailable")
    )
    monkeypatch.setattr(tracker_module, "tracing_context", contexts)
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=lambda **kwargs: object(),
        trace_factory=ForbiddenTraceFactory(),
    )

    async with tracker.session_span("session-1", "question"):
        pass

    assert tracker.metrics[-1].success is True
    assert tracker.errors[-1].details["stage"] == "tracing_context_enter"


@pytest.mark.asyncio
async def test_tracing_context_exit_failure_preserves_operation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = RecordingTracingContextFactory(
        exit_error=ConnectionError("context close")
    )
    manager = FakeTraceManager(
        FakeRun("https://smith.langchain.com/o/example/r/failure")
    )
    monkeypatch.setattr(tracker_module, "tracing_context", contexts)
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

    metadata = manager.run.end_calls[-1]["metadata"]
    assert metadata["success"] is False
    assert metadata["error_type"] == "ValueError"
    assert "latency_ms" in metadata
    assert metadata["trace_url"] == "https://smith.langchain.com/o/example/r/failure"
    assert tracker.errors[-1].details["stage"] == "tracing_context_exit"


class RecordingClientFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        api_key: str,
        tracing_error_callback: Callable[[Exception], None],
        anonymizer: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> object:
        self.calls.append(
            {
                "api_key": api_key,
                "tracing_error_callback": tracing_error_callback,
                "anonymizer": anonymizer,
            }
        )
        return object()


def test_client_background_transport_callback_records_failure_without_raising() -> None:
    client_factory = RecordingClientFactory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=client_factory,
        trace_factory=ForbiddenTraceFactory(),
    )
    callback = client_factory.calls[0]["tracing_error_callback"]

    callback(ConnectionError("background upload failed"))

    assert [error.details["stage"] for error in tracker.errors] == [
        "background_transport"
    ]


class FailingErrorList(list[Any]):
    def append(self, value: Any) -> None:
        del value
        raise RuntimeError("cannot record fallback error")


@pytest.mark.asyncio
async def test_langsmith_failure_recorder_failure_does_not_mask_research_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = FakeTraceManager(
        FakeRun("https://smith.langchain.com/o/example/r/recorder-failure"),
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
    monkeypatch.setattr(tracker, "_errors", FailingErrorList())

    with pytest.raises(ValueError, match="research failed"):
        async with tracker.session_span("session-1", "question"):
            raise ValueError("research failed")

    assert tracker.metrics[-1].success is False
    assert tracker.events[-1].event_type == "observability.span.completed"


@pytest.mark.asyncio
async def test_remote_payloads_and_client_anonymizer_redacts_secrets(
) -> None:
    secret = "sentinel-langsmith-secret"
    client_factory = RecordingClientFactory()
    trace_factory = RecordingTraceFactory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key=secret,
        ),
        client_factory=client_factory,
        trace_factory=trace_factory,
    )

    with pytest.raises(ValueError, match="research failed"):
        async with tracker.session_span(
            "session-1",
            f"question containing {secret}",
        ):
            async with tracker.llm_span(
                "gpt-4o",
                {
                    "authorization": f"Bearer {secret}",
                    "nested": [
                        {"password": "provider-password"},
                        {"apikey": "alternate-key"},
                        {"access_token": "access-secret"},
                        {"refresh_token": "refresh-secret"},
                        {"secret": "nested-secret"},
                        {"openai_api_key": "openai-secret"},
                        {"db_password": "database-secret"},
                        {"auth_token": "auth-secret"},
                        f"free form {secret}",
                    ],
                },
            ) as llm:
                llm.set_outputs(
                    {
                        "api_key": "provider-key",
                        "tavily_api_key": "tavily-secret",
                        "x_api_key": "x-api-secret",
                        "response": f"response containing {secret}",
                    }
                )
                llm.set_token_usage(input_tokens=3, output_tokens=2)
                raise ValueError(f"research failed with {secret}")

    anonymized = client_factory.calls[0]["anonymizer"](
        {
            "credential": "service-credential",
            "Authorization": "Bearer another-secret",
            "client_secret": "client-secret-value",
            "auth_token": "anonymizer-auth-secret",
            "password_hint": "first pet",
            "secretary": "office contact",
            "message": f"anonymizer containing {secret}",
            "token_usage": {"input_tokens": 3, "output_tokens": 2},
        }
    )
    remote_payloads = {
        "session_inputs": trace_factory.calls[0]["inputs"],
        "llm_inputs": trace_factory.calls[1]["inputs"],
        "llm_end": trace_factory.managers[1].run.end_calls[-1],
        "anonymized": anonymized,
    }
    serialized = repr(remote_payloads)
    assert secret not in serialized
    assert "provider-password" not in serialized
    assert "provider-key" not in serialized
    assert "alternate-key" not in serialized
    assert "access-secret" not in serialized
    assert "refresh-secret" not in serialized
    assert "nested-secret" not in serialized
    assert "openai-secret" not in serialized
    assert "database-secret" not in serialized
    assert "auth-secret" not in serialized
    assert "tavily-secret" not in serialized
    assert "x-api-secret" not in serialized
    assert "service-credential" not in serialized
    assert "another-secret" not in serialized
    assert "client-secret-value" not in serialized
    assert "anonymizer-auth-secret" not in serialized
    assert anonymized["password_hint"] == "first pet"
    assert anonymized["secretary"] == "office contact"
    assert anonymized["token_usage"] == {"input_tokens": 3, "output_tokens": 2}
    llm_exit = trace_factory.managers[1].exit_calls[-1]
    assert llm_exit[0] is ValueError
    assert secret not in str(llm_exit[1])
    assert str(llm_exit[1]) == "research failed with [REDACTED]"
    assert llm_exit[2] is None
    session_exit = trace_factory.managers[0].exit_calls[-1]
    assert session_exit[0] is ValueError
    assert secret not in str(session_exit[1])
    assert session_exit[2] is None


@pytest.mark.asyncio
async def test_remote_run_names_and_initial_metadata_redact_configured_secret(
) -> None:
    secret = "sentinel-context-secret"
    trace_factory = RecordingTraceFactory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key=secret,
        ),
        client_factory=RecordingClientFactory(),
        trace_factory=trace_factory,
    )

    async with tracker.session_span(f"session-{secret}", "question"):
        async with tracker.agent_span(f"agent-{secret}"):
            async with tracker.react_iteration_span(0):
                async with tracker.llm_span(f"model-{secret}", {}):
                    pass
                async with tracker.tool_span(f"tool-{secret}", {}):
                    pass

    assert secret not in repr(trace_factory.calls)
    assert trace_factory.calls[1]["name"] == "agent.agent-[REDACTED]"
    assert trace_factory.calls[3]["name"] == "llm.model-[REDACTED]"
    assert trace_factory.calls[4]["name"] == "tool.tool-[REDACTED]"
    assert secret in repr(tracker.events)


@pytest.mark.asyncio
async def test_started_event_precedes_remote_entry_and_completion_keeps_url() -> None:
    observed_events: list[tuple[str, ...]] = []
    tracker: Tracker

    def trace_factory(name: str, run_type: str, **kwargs: Any) -> FakeTraceManager:
        del name, run_type, kwargs
        observed_events.append(tuple(event.event_type for event in tracker.events))
        return FakeTraceManager(
            FakeRun("https://smith.langchain.com/o/example/r/ordered")
        )

    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=RecordingClientFactory(),
        trace_factory=trace_factory,
    )

    async with tracker.session_span("session-1", "question"):
        pass

    assert observed_events == [("observability.span.started",)]
    assert tracker.events[-1].metadata["trace_url"].endswith("/ordered")
    assert tracker.events[-1].metadata["session_id"] == "session-1"
    assert tracker.events[0].metadata["session_id"] == "session-1"
    assert "trace_url" not in tracker.events[0].metadata
    assert tracker.metrics[-1].trace_url.endswith("/ordered")


@pytest.mark.asyncio
async def test_invalid_public_child_span_arguments_fail_before_yielding() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ValidationError):
            tracker.agent_span("   ")
        async with tracker.agent_span("planner"):
            with pytest.raises(ValidationError):
                tracker.react_iteration_span(-1)
            with pytest.raises(ValidationError):
                tracker.llm_span("   ", {})
            with pytest.raises(ValidationError):
                tracker.tool_span("   ", {})


@pytest.mark.asyncio
async def test_negative_tool_retry_count_fails_before_entering_body() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    body_entered = False

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ValidationError):
            async with tracker.tool_span("web_search", {}, retry_count=-1):
                body_entered = True

    assert body_entered is False


@pytest.mark.asyncio
async def test_tool_span_uses_retry_count_reported_during_execution() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))

    async with tracker.session_span("session-1", "question"):
        async with tracker.tool_span("web_search", {"query": "topic"}) as span:
            span.set_retry_count(2)
            span.set_outputs({"success": True, "result_count": 1})

    metric = next(item for item in tracker.metrics if isinstance(item, ToolMetric))
    assert metric.retry_count == 2


@pytest.mark.asyncio
async def test_span_handle_rejects_negative_retry_count() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))

    async with tracker.session_span("session-1", "question"):
        async with tracker.tool_span("web_search", {"query": "topic"}) as span:
            with pytest.raises(ValidationError):
                span.set_retry_count(-1)

def _tracker_for_validation_test(tracing_enabled: bool) -> Tracker:
    runtime = LangSmithRuntimeConfig(
        tracing_enabled=tracing_enabled,
        project="deep-research-tests" if tracing_enabled else "",
        api_key="secret-key" if tracing_enabled else None,
    )
    return Tracker(
        runtime,
        client_factory=(
            RecordingClientFactory() if tracing_enabled else ForbiddenClientFactory()
        ),
        trace_factory=(
            RecordingTraceFactory() if tracing_enabled else ForbiddenTraceFactory()
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tracing_enabled", [False, True])
@pytest.mark.parametrize("invalid_question", [None, "   "])
async def test_invalid_session_question_fails_before_entering_body(
    tracing_enabled: bool,
    invalid_question: Any,
) -> None:
    tracker = _tracker_for_validation_test(tracing_enabled)
    body_entered = False

    with pytest.raises(ValidationError):
        async with tracker.session_span("session-1", invalid_question):
            body_entered = True

    assert body_entered is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tracing_enabled", [False, True])
@pytest.mark.parametrize("span_kind", ["llm", "tool"])
@pytest.mark.parametrize(
    "invalid_inputs",
    [None, {1: "value"}, {b"key": "value"}, {"bad": object()}],
)
async def test_invalid_child_inputs_fail_before_entering_body(
    tracing_enabled: bool,
    span_kind: str,
    invalid_inputs: Any,
) -> None:
    tracker = _tracker_for_validation_test(tracing_enabled)
    body_entered = False

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ValidationError):
            span = (
                tracker.llm_span("gpt-4o", invalid_inputs)
                if span_kind == "llm"
                else tracker.tool_span("web_search", invalid_inputs)
            )
            async with span:
                body_entered = True

    assert body_entered is False


@pytest.mark.asyncio
@pytest.mark.parametrize("span_kind", ["llm", "tool"])
async def test_child_inputs_are_copied_before_context_manager_entry(
    span_kind: str,
) -> None:
    trace_factory = RecordingTraceFactory()
    tracker = Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=True,
            project="deep-research-tests",
            api_key="secret-key",
        ),
        client_factory=RecordingClientFactory(),
        trace_factory=trace_factory,
    )
    inputs = {"value": "original"}

    async with tracker.session_span("session-1", "question"):
        span = (
            tracker.llm_span("gpt-4o", inputs)
            if span_kind == "llm"
            else tracker.tool_span("web_search", inputs)
        )
        inputs["value"] = "mutated"
        async with span:
            pass

    assert trace_factory.calls[1]["inputs"] == {"value": "original"}


@pytest.mark.asyncio
async def test_overlapping_tasks_keep_trace_context_isolated() -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    entered = {
        "session-1": asyncio.Event(),
        "session-2": asyncio.Event(),
    }

    async def run_session(session_id: str, agent_name: str, peer_id: str) -> None:
        async with tracker.session_span(session_id, "question"):
            async with tracker.agent_span(agent_name):
                entered[session_id].set()
                await entered[peer_id].wait()
                await asyncio.sleep(0)
                context = current_trace_context()
                assert context is not None
                assert context.session_id == session_id
                assert context.agent_name == agent_name
            context = current_trace_context()
            assert context is not None
            assert context.session_id == session_id
            assert context.agent_name is None
        assert current_trace_context() is None

    await asyncio.gather(
        run_session("session-1", "planner", "session-2"),
        run_session("session-2", "writer", "session-1"),
    )
    assert current_trace_context() is None


@pytest.mark.asyncio
async def test_local_finalization_failure_does_not_replace_research_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    original_record_span_event = tracker._record_span_event

    def fail_completion(*args: Any, **kwargs: Any) -> None:
        if kwargs["phase"] == "completed":
            raise RuntimeError("local completion failed")
        original_record_span_event(*args, **kwargs)

    monkeypatch.setattr(tracker, "_record_span_event", fail_completion)

    with pytest.raises(ValueError, match="research failed"):
        async with tracker.session_span("session-1", "question"):
            raise ValueError("research failed")

    assert tracker.errors[-1].details["stage"] == "local_finalization"
