"""Async-safe trace context and LangSmith runtime configuration."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, model_validator

from deep_research.utils.config import LangSmithConfig


class LangSmithRuntimeConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_default=True
    )

    tracing_enabled: bool = False
    project: str = ""
    api_key: SecretStr | None = None

    @model_validator(mode="after")
    def require_enabled_credentials(self) -> "LangSmithRuntimeConfig":
        if not self.tracing_enabled:
            return self
        if not self.project:
            raise ValueError(
                "LANGSMITH_PROJECT is required when LANGSMITH_TRACING=true"
            )
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ValueError(
                "LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true"
            )
        return self


class TraceContext(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_default=True
    )

    session_id: str = Field(min_length=1)
    agent_name: str | None = Field(default=None, min_length=1)
    tool_name: str | None = Field(default=None, min_length=1)
    iteration: int | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, min_length=1)
    trace_url: str | None = Field(default=None, min_length=1)


_CURRENT_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "deep_research_trace_context", default=None
)


def load_langsmith_runtime_config(
    config: LangSmithConfig, *, environ: Mapping[str, str] | None = None
) -> LangSmithRuntimeConfig:
    source = os.environ if environ is None else environ
    raw_api_key = source.get("LANGSMITH_API_KEY", "").strip()
    project = config.project.strip()
    if config.tracing_enabled and not project:
        raise ValueError("LANGSMITH_PROJECT is required when LANGSMITH_TRACING=true")
    if config.tracing_enabled and not raw_api_key:
        raise ValueError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true")
    return LangSmithRuntimeConfig(
        tracing_enabled=config.tracing_enabled,
        project=project,
        api_key=SecretStr(raw_api_key) if raw_api_key else None,
    )


def current_trace_context() -> TraceContext | None:
    return _CURRENT_TRACE_CONTEXT.get()


@contextmanager
def bind_trace_context(context: TraceContext) -> Iterator[TraceContext]:
    token = _CURRENT_TRACE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_TRACE_CONTEXT.reset(token)


def build_trace_metadata(
    context: TraceContext, *, extra: Mapping[str, JsonValue] | None = None
) -> dict[str, JsonValue]:
    metadata = dict(extra or {})
    metadata.update(
        {key: value for key, value in context.model_dump().items() if value is not None}
    )
    return metadata
