import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError

from deep_research.observability import ToolMetric
from deep_research.observability.tracker import SpanHandle
from deep_research.tools.base import (
    BaseTool,
    ToolCallContext,
    ToolError,
    ToolExecution,
    ToolExecutionError,
    ToolResult,
)


class SuccessfulTool(BaseTool):
    name = "successful"
    description = "Return one value."
    input_schema = {"value": "string"}
    output_schema = {"echo": "string"}

    async def _execute(
        self, context: ToolCallContext, **kwargs: object
    ) -> ToolExecution:
        context.record_retry()
        return ToolExecution(
            data={"echo": kwargs["value"]},
            output_summary={"keys": ["echo"]},
            metadata={"provider": "fake"},
        )


class FailingTool(BaseTool):
    name = "failing"
    description = "Raise a recoverable failure."
    input_schema = {}
    output_schema = {}

    async def _execute(
        self, context: ToolCallContext, **kwargs: object
    ) -> ToolExecution:
        context.record_retry()
        raise ToolExecutionError(
            "provider timed out",
            error_type="TimeoutError",
            details={"attempts": 2},
        )


class CancelledTool(BaseTool):
    name = "cancelled"
    description = "Propagate cancellation."
    input_schema = {}
    output_schema = {}

    async def _execute(
        self, context: ToolCallContext, **kwargs: object
    ) -> ToolExecution:
        raise asyncio.CancelledError()


class InvalidMetadataTool(BaseTool):
    name = "invalid-metadata"
    description = "Return metadata that cannot serialize as JSON."
    input_schema = {}
    output_schema = {}

    async def _execute(
        self, context: ToolCallContext, **kwargs: object
    ) -> ToolExecution:
        return ToolExecution(
            data=None,
            output_summary={},
            metadata={"invalid": object()},  # type: ignore[dict-item]
        )


class SummaryCannotOverrideSuccessTool(BaseTool):
    name = "summary-cannot-override-success"
    description = "Attempt to override the framework success field."
    input_schema = {}
    output_schema = {}

    async def _execute(
        self, context: ToolCallContext, **kwargs: object
    ) -> ToolExecution:
        return ToolExecution(data=None, output_summary={"success": False})


class RecordingToolTracker:
    def __init__(self, span: SpanHandle) -> None:
        self.span = span

    @asynccontextmanager
    async def tool_span(self, name: str, inputs: dict[str, object]):
        del name, inputs
        yield self.span


def test_tool_result_forbids_unknown_fields_and_invalid_outcomes() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            tool_name="test",
            success=True,
            latency_ms=0,
            unexpected=True,
        )
    with pytest.raises(ValidationError, match="cannot include an error"):
        ToolResult(
            tool_name="test",
            success=True,
            error=ToolError(type="Error", message="failed"),
            latency_ms=0,
        )
    with pytest.raises(ValidationError, match="require an error"):
        ToolResult(tool_name="test", success=False, latency_ms=0)


@pytest.mark.asyncio
async def test_successful_execution_returns_data_and_records_metric(tracker) -> None:
    async with tracker.session_span("session-1", "question"):
        result = await SuccessfulTool(tracker).execute(value="hello")

    assert result.model_dump(mode="json") == {
        "tool_name": "successful",
        "success": True,
        "data": {"echo": "hello"},
        "error": None,
        "latency_ms": result.latency_ms,
        "metadata": {"provider": "fake", "retry_count": 1},
    }
    assert result.latency_ms > 0
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.success is True
    assert metric.retry_count == 1


@pytest.mark.asyncio
async def test_invalid_success_result_is_recorded_as_failed_tool_metric(
    tracker,
) -> None:
    async with tracker.session_span("session-1", "question"):
        result = await InvalidMetadataTool(tracker).execute()

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "ValidationError"
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.success is False
    assert metric.error_type == "ValidationError"


@pytest.mark.asyncio
async def test_success_summary_cannot_override_framework_success() -> None:
    span = SpanHandle(context=None)  # type: ignore[arg-type]
    tracker = RecordingToolTracker(span)

    result = await SummaryCannotOverrideSuccessTool(tracker).execute()

    assert result.success is True
    assert span.outputs == {"success": True}


@pytest.mark.asyncio
async def test_failing_execution_returns_recoverable_error_and_records_metric(
    tracker,
) -> None:
    async with tracker.session_span("session-1", "question"):
        result = await FailingTool(tracker).execute()

    assert result.success is False
    assert result.error == ToolError(
        type="TimeoutError",
        message="provider timed out",
        recoverable=True,
        details={"attempts": 2},
    )
    assert result.metadata == {"retry_count": 1}
    metric = next(
        metric for metric in tracker.metrics if isinstance(metric, ToolMetric)
    )
    assert metric.success is False
    assert metric.error_type == "ToolExecutionError"
    assert metric.retry_count == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_without_a_tool_result(tracker) -> None:
    async with tracker.session_span("session-1", "question"):
        with pytest.raises(asyncio.CancelledError):
            await CancelledTool(tracker).execute()