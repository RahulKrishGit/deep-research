"""Stable Pydantic schemas for local and remote observability metrics."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeInt: TypeAlias = Annotated[int, Field(ge=0)]
NonNegativeFloat: TypeAlias = Annotated[float, Field(ge=0)]


class MetricModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class OutcomeMetric(MetricModel):
    latency_ms: NonNegativeFloat
    success: bool
    error_type: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_error_type_matches_success(self) -> "OutcomeMetric":
        if self.success and self.error_type is not None:
            raise ValueError("successful metrics cannot include error_type")
        if not self.success and self.error_type is None:
            raise ValueError("failed metrics require error_type")
        return self


class SessionMetric(OutcomeMetric):
    metric_type: Literal["session"] = "session"
    session_id: str = Field(min_length=1)
    trace_url: str | None = Field(default=None, min_length=1)


class AgentMetric(OutcomeMetric):
    metric_type: Literal["agent"] = "agent"
    session_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    scope: Literal["agent", "react_iteration"]
    iteration: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_iteration_matches_scope(self) -> "AgentMetric":
        if self.scope == "react_iteration" and self.iteration is None:
            raise ValueError("react_iteration metrics require iteration")
        if self.scope == "agent" and self.iteration is not None:
            raise ValueError("agent metrics cannot include iteration")
        return self


class ApiMetric(OutcomeMetric):
    metric_type: Literal["api"] = "api"
    session_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    method: str = Field(min_length=1)


class ToolMetric(OutcomeMetric):
    metric_type: Literal["tool"] = "tool"
    session_id: str = Field(min_length=1)
    agent_name: str | None = Field(default=None, min_length=1)
    tool_name: str = Field(min_length=1)
    iteration: int | None = Field(default=None, ge=0)
    retry_count: NonNegativeInt = 0


class TokenUsageMetric(OutcomeMetric):
    metric_type: Literal["token_usage"] = "token_usage"
    session_id: str = Field(min_length=1)
    agent_name: str | None = Field(default=None, min_length=1)
    model: str = Field(min_length=1)
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    total_tokens: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_total_tokens(self) -> "TokenUsageMetric":
        expected_total = self.input_tokens + self.output_tokens
        if self.total_tokens != expected_total:
            raise ValueError(
                "total_tokens must equal input_tokens + output_tokens"
            )
        return self


MemoryLayer: TypeAlias = Literal["scratchpad", "long_term", "procedural"]


class MemoryMetric(OutcomeMetric):
    metric_type: Literal["memory"] = "memory"
    session_id: str = Field(min_length=1)
    agent_name: str | None = Field(default=None, min_length=1)
    memory_layer: MemoryLayer
    operation: str = Field(min_length=1)
    entry_type: str | None = Field(default=None, min_length=1)
    top_k: int | None = Field(default=None, ge=1)
    result_count: NonNegativeInt = 0


MetricRecord: TypeAlias = (
    SessionMetric
    | AgentMetric
    | ApiMetric
    | ToolMetric
    | TokenUsageMetric
    | MemoryMetric
)
