"""Shared contracts and observable execution lifecycle for research tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from deep_research.observability import SpanHandle, Tracker


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool = True
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_name: str = Field(min_length=1)
    success: bool
    data: JsonValue | None = None
    error: ToolError | None = None
    latency_ms: float = Field(ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_outcome(self) -> "ToolResult":
        if self.success and self.error is not None:
            raise ValueError("successful tool results cannot include an error")
        if not self.success and self.error is None:
            raise ValueError("failed tool results require an error")
        return self


@dataclass(slots=True)
class ToolExecution:
    data: JsonValue
    output_summary: dict[str, JsonValue]
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class ToolExecutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_type: str | None = None,
        recoverable: bool = True,
        details: dict[str, JsonValue] | None = None,
        data: JsonValue | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type or type(self).__name__
        self.recoverable = recoverable
        self.details = details or {}
        self.data = data


@dataclass(slots=True)
class ToolCallContext:
    span: SpanHandle
    retry_count: int = 0

    def record_retry(self) -> None:
        self.retry_count += 1
        self.span.set_retry_count(self.retry_count)


class BaseTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, JsonValue]]
    output_schema: ClassVar[dict[str, JsonValue]]

    def __init__(self, tracker: Tracker) -> None:
        self._tracker = tracker

    async def execute(self, **kwargs: Any) -> ToolResult:
        started_at = perf_counter()
        context: ToolCallContext | None = None
        try:
            async with self._tracker.tool_span(
                self.name, self._observability_inputs(kwargs)
            ) as span:
                context = ToolCallContext(span=span)
                execution = await self._execute(context, **kwargs)
                result = ToolResult(
                    tool_name=self.name,
                    success=True,
                    data=execution.data,
                    latency_ms=(perf_counter() - started_at) * 1000,
                    metadata={
                        **execution.metadata,
                        "retry_count": context.retry_count,
                    },
                )
                span.set_outputs({**execution.output_summary, "success": True})
            return result
        except Exception as error:
            failure = (
                error
                if isinstance(error, ToolExecutionError)
                else ToolExecutionError(
                    str(error) or type(error).__name__,
                    error_type=type(error).__name__,
                )
            )
            return ToolResult(
                tool_name=self.name,
                success=False,
                data=failure.data,
                error=ToolError(
                    type=failure.error_type,
                    message=str(failure),
                    recoverable=failure.recoverable,
                    details=failure.details,
                ),
                latency_ms=(perf_counter() - started_at) * 1000,
                metadata={
                    "retry_count": context.retry_count if context else 0,
                },
            )

    def _observability_inputs(
        self, kwargs: dict[str, Any]
    ) -> dict[str, JsonValue]:
        return dict(kwargs)

    @abstractmethod
    async def _execute(
        self, context: ToolCallContext, **kwargs: Any
    ) -> ToolExecution:
        raise NotImplementedError