import asyncio

import pytest
from pydantic import ValidationError

from deep_research.observability import ToolMetric
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