# LangSmith Observability Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable observability layer that records local structured events and metrics for every research span, optionally mirrors those spans to LangSmith, and never lets LangSmith failures interrupt research execution.

**Architecture:** Keep all direct LangSmith SDK usage inside `deep_research.observability.tracker`; downstream providers, tools, agents, and orchestration code depend only on exported tracker/context/metric contracts. Use `contextvars` to carry session and nested span metadata through async code, Pydantic models for stable serializable metrics, and one async span engine that always records locally before optionally entering a LangSmith `trace` context.

**Tech Stack:** Python 3.11+, Pydantic 2, LangSmith Python SDK 0.10+, `contextvars`, pytest, Ruff

## Global Constraints

- Preserve `requires-python = ">=3.11"`.
- Load `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, and `LANGSMITH_TRACING=true|false`; do not store the raw API key in `ConfigSettings` or serialized observability data.
- Enabled mode emits LangSmith traces and also records local events, metrics, and recoverable tracing errors.
- Disabled mode performs no LangSmith client or trace calls and records local structured events and metrics only.
- LangSmith client creation, span start, URL lookup, span finalization, and span exit failures must not break research execution.
- Exceptions raised by the research operation itself must still propagate after local failure metadata is recorded.
- Standard async wrappers must cover session, agent, ReAct iteration, LLM, and tool spans before LangGraph exists.
- Stable metadata keys are `session_id`, `agent_name`, `tool_name`, `iteration`, `model`, `token_usage`, `latency_ms`, `success`, `error_type`, and `trace_url` when available.
- Do not add LangGraph graph behavior, real agents, post-session quality evaluators, or dashboard customization.
- Tests must use fake or mocked LangSmith collaborators and make no real LangSmith API calls.

## File Structure

- Modify `pyproject.toml` to add the LangSmith SDK runtime dependency.
- Modify `src/deep_research/utils/config.py` to honor the exact `LANGSMITH_TRACING` environment name while preserving secret exclusion from `ConfigSettings`.
- Create `src/deep_research/observability/context.py` for runtime settings, async-safe nested trace context, and metadata projection.
- Create `src/deep_research/observability/metrics.py` for session, agent/ReAct, tool, and token-usage metric schemas.
- Create `src/deep_research/observability/tracker.py` for the public async span API, local event/error/metric collection, LangSmith integration, and failure fallback.
- Modify `src/deep_research/observability/__init__.py` to expose the stable public surface without requiring downstream LangSmith imports.
- Modify `.env.example` and `README.md` to document the tracing toggle, enabled-mode settings, and tracker usage.
- Modify `tests/test_config.py` and `tests/test_imports.py`; create `tests/test_observability_context.py`, `tests/test_observability_metrics.py`, and `tests/test_observability_tracker.py`.

---

### Task 1: Runtime Configuration And Trace Context

**Files:**
- Modify: `pyproject.toml:10`
- Modify: `src/deep_research/utils/config.py:71`
- Create: `src/deep_research/observability/context.py`
- Modify: `tests/test_config.py:75`
- Create: `tests/test_observability_context.py`

**Interfaces:**
- Consumes: `deep_research.utils.config.LangSmithConfig(tracing_enabled: bool, project: str)` and process environment values.
- Produces: `LangSmithRuntimeConfig`, `load_langsmith_runtime_config(config: LangSmithConfig, *, environ: Mapping[str, str] | None = None) -> LangSmithRuntimeConfig`, `TraceContext`, `bind_trace_context(context: TraceContext) -> Iterator[TraceContext]`, `current_trace_context() -> TraceContext | None`, and `build_trace_metadata(context: TraceContext, *, extra: Mapping[str, JsonValue] | None = None) -> dict[str, JsonValue]`.

- [ ] **Step 1: Write failing configuration and context tests**

In `tests/test_config.py`, replace:

```python
("LANGSMITH_TRACING_ENABLED", ("langsmith", "tracing_enabled"), "true", True),
```

with:

```python
("LANGSMITH_TRACING", ("langsmith", "tracing_enabled"), "true", True),
```

Create `tests/test_observability_context.py`:

```python
"""Tests for LangSmith runtime settings and nested trace context."""

from collections.abc import Mapping

import pytest

from deep_research.observability.context import (
    TraceContext,
    bind_trace_context,
    build_trace_metadata,
    current_trace_context,
    load_langsmith_runtime_config,
)


def test_disabled_runtime_config_does_not_require_secrets() -> None:
    runtime = load_langsmith_runtime_config(
        LangSmithConfig(tracing_enabled=False, project=""),
        environ={},
    )

    assert runtime.tracing_enabled is False
    assert runtime.project == ""
    assert runtime.api_key is None


def test_enabled_runtime_config_loads_api_key_without_serializing_it() -> None:
    runtime = load_langsmith_runtime_config(
        LangSmithConfig(tracing_enabled=True, project="deep-research-tests"),
        environ={"LANGSMITH_API_KEY": "secret-key"},
    )

    assert runtime.api_key is not None
    assert runtime.api_key.get_secret_value() == "secret-key"
    assert "secret-key" not in runtime.model_dump_json()


@pytest.mark.parametrize(
    ("config", "environ", "missing_name"),
    [
        (
            LangSmithConfig(tracing_enabled=True, project=""),
            {"LANGSMITH_API_KEY": "secret-key"},
            "LANGSMITH_PROJECT",
        ),
        (
            LangSmithConfig(tracing_enabled=True, project="deep-research-tests"),
            {},
            "LANGSMITH_API_KEY",
        ),
    ],
)
def test_enabled_runtime_config_requires_project_and_api_key(
    config: LangSmithConfig,
    environ: Mapping[str, str],
    missing_name: str,
) -> None:
    with pytest.raises(ValueError, match=missing_name):
        load_langsmith_runtime_config(config, environ=environ)


def test_nested_trace_context_restores_parent() -> None:
    session = TraceContext(session_id="session-1")
    agent = session.model_copy(update={"agent_name": "planner"})

    assert current_trace_context() is None
    with bind_trace_context(session):
        assert current_trace_context() == session
        with bind_trace_context(agent):
            assert current_trace_context() == agent
        assert current_trace_context() == session
    assert current_trace_context() is None


def test_build_trace_metadata_filters_none_and_protects_context_keys() -> None:
    context = TraceContext(
        session_id="session-1",
        agent_name="planner",
        iteration=2,
    )

    metadata = build_trace_metadata(
        context,
        extra={"span_kind": "react", "session_id": "wrong-session"},
    )

    assert metadata == {
        "span_kind": "react",
        "session_id": "session-1",
        "agent_name": "planner",
        "iteration": 2,
    }
```

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
python -m pytest tests/test_config.py tests/test_observability_context.py -v
```

Expected: collection fails because `deep_research.observability.context` does not exist, and the config override remains unmapped.

- [ ] **Step 3: Add dependency and exact environment override**

Update `pyproject.toml`:

```toml
dependencies = [
    "langsmith>=0.10",
    "pydantic>=2",
    "pyyaml",
]
```

In `src/deep_research/utils/config.py`, replace only the tracing key:

```python
"LANGSMITH_TRACING": ("langsmith", "tracing_enabled"),
```

Do not add `api_key` to `LangSmithConfig`; preserve the existing secret-exclusion contract.

- [ ] **Step 4: Implement runtime settings and context helpers**

Create `src/deep_research/observability/context.py`:

```python
"""Async-safe trace context and LangSmith runtime configuration."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from deep_research.utils.config import LangSmithConfig


class LangSmithRuntimeConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )

    tracing_enabled: bool = False
    project: str = ""
    api_key: SecretStr | None = None


class TraceContext(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )

    session_id: str = Field(min_length=1)
    agent_name: str | None = Field(default=None, min_length=1)
    tool_name: str | None = Field(default=None, min_length=1)
    iteration: int | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, min_length=1)
    trace_url: str | None = Field(default=None, min_length=1)


_CURRENT_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "deep_research_trace_context",
    default=None,
)


def load_langsmith_runtime_config(
    config: LangSmithConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> LangSmithRuntimeConfig:
    source = os.environ if environ is None else environ
    raw_api_key = source.get("LANGSMITH_API_KEY", "").strip()
    project = config.project.strip()

    if config.tracing_enabled and not project:
        raise ValueError(
            "LANGSMITH_PROJECT is required when LANGSMITH_TRACING=true"
        )
    if config.tracing_enabled and not raw_api_key:
        raise ValueError(
            "LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true"
        )

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
    context: TraceContext,
    *,
    extra: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    metadata = dict(extra or {})
    metadata.update(
        {
            key: value
            for key, value in context.model_dump().items()
            if value is not None
        }
    )
    return metadata
```

- [ ] **Step 5: Install and rerun focused tests**

Run:

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/test_config.py tests/test_observability_context.py -v
```

Expected: all focused tests pass, the runtime config JSON masks the API key, and no LangSmith network call occurs.

- [ ] **Step 6: Commit runtime configuration**

```bash
git add pyproject.toml src/deep_research/utils/config.py src/deep_research/observability/context.py tests/test_config.py tests/test_observability_context.py
git commit -m "feat: add LangSmith runtime context"
```


### Task 2: Stable Observability Metric Schemas

**Files:**
- Create: `src/deep_research/observability/metrics.py`
- Create: `tests/test_observability_metrics.py`

**Interfaces:**
- Consumes: primitive session, agent, iteration, model, token, tool, latency, success, error, retry, and trace URL values from later tracker spans.
- Produces: `SessionMetric`, `AgentMetric`, `ToolMetric`, `TokenUsageMetric`, and the `MetricRecord` union used by `Tracker.metrics`.

- [ ] **Step 1: Write failing metric schema tests**

Create `tests/test_observability_metrics.py`:

```python
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
```

- [ ] **Step 2: Run metric tests and verify failure**

Run:

```bash
python -m pytest tests/test_observability_metrics.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'deep_research.observability.metrics'`.

- [ ] **Step 3: Implement the metric models**

Create `src/deep_research/observability/metrics.py`:

```python
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


MetricRecord: TypeAlias = (
    SessionMetric | AgentMetric | ToolMetric | TokenUsageMetric
)
```

- [ ] **Step 4: Run metric tests and lint**

Run:

```bash
python -m pytest tests/test_observability_metrics.py -v
ruff check src/deep_research/observability/metrics.py tests/test_observability_metrics.py
```

Expected: all metric tests pass and Ruff prints `All checks passed!`.

- [ ] **Step 5: Commit metric schemas**

```bash
git add src/deep_research/observability/metrics.py tests/test_observability_metrics.py
git commit -m "feat: add observability metric schemas"
```


### Task 3: Disabled Tracker And Nested Span API

**Files:**
- Create: `src/deep_research/observability/tracker.py`
- Create: `tests/test_observability_tracker.py`

**Interfaces:**
- Consumes: `LangSmithRuntimeConfig`, trace-context helpers from Task 1, metric records from Task 2, and shared `ResearchEvent`/`ResearchError` models.
- Produces: `TokenUsage`, `SpanHandle`, `Tracker.from_config(config: LangSmithConfig, *, environ: Mapping[str, str] | None = None, client_factory: ClientFactory = Client, trace_factory: TraceFactory = trace) -> Tracker`, `Tracker.session_span(session_id: str, question: str) -> AbstractAsyncContextManager[SpanHandle]`, `Tracker.agent_span(agent_name: str) -> AbstractAsyncContextManager[SpanHandle]`, `Tracker.react_iteration_span(iteration: int) -> AbstractAsyncContextManager[SpanHandle]`, `Tracker.llm_span(model: str, inputs: Mapping[str, JsonValue]) -> AbstractAsyncContextManager[SpanHandle]`, `Tracker.tool_span(tool_name: str, inputs: Mapping[str, JsonValue], *, retry_count: int = 0) -> AbstractAsyncContextManager[SpanHandle]`, plus snapshot properties `events`, `errors`, and `metrics`.

- [ ] **Step 1: Add async test support and write failing disabled-mode tests**

Update `pyproject.toml` development dependencies:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=7",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
]
```

Create `tests/test_observability_tracker.py` with fake LangSmith collaborators and disabled-mode tests:

```python
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
from deep_research.utils.config import LangSmithConfig


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

                async with tracker.tool_span(
                    "web_search",
                    {"query": "Rayleigh scattering"},
                    retry_count=1,
                ):
                    assert current_trace_context().tool_name == "web_search"

    assert current_trace_context() is None
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
        isinstance(metric, TokenUsageMetric)
        and metric.total_tokens == 18
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
```

Remove the accidental leading `+` characters after pasting; they mark newly added lines in this plan, not Python syntax.

- [ ] **Step 2: Run disabled tracker tests and verify failure**

Run:

```bash
python -m pytest tests/test_observability_tracker.py -v
```

Expected: collection fails because `deep_research.observability.tracker` does not exist.

- [ ] **Step 3: Implement tracker contracts and local span engine**

Create `src/deep_research/observability/tracker.py` with these public contracts and private aliases:

```python
"""Public observability API with local recording and LangSmith fallback."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
)
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal, Protocol, TypeAlias

from langsmith import Client, trace, tracing_context
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from deep_research.observability.context import (
    LangSmithRuntimeConfig,
    TraceContext,
    bind_trace_context,
    build_trace_metadata,
    current_trace_context,
    load_langsmith_runtime_config,
)
from deep_research.observability.metrics import (
    AgentMetric,
    MetricRecord,
    SessionMetric,
    TokenUsageMetric,
    ToolMetric,
)
from deep_research.utils.config import LangSmithConfig
from deep_research.utils.types import ResearchError, ResearchEvent


SpanKind: TypeAlias = Literal["session", "agent", "react_iteration", "llm", "tool"]
RunType: TypeAlias = Literal["chain", "llm", "tool"]


class RunLike(Protocol):
    metadata: dict[str, Any]

    def end(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store completion data on a LangSmith-compatible run."""
        raise NotImplementedError

    def get_url(self) -> str:
        """Return the LangSmith URL for a root run."""
        raise NotImplementedError


class AsyncTraceManager(Protocol):
    async def __aenter__(self) -> RunLike:
        """Open and return a LangSmith-compatible run."""
        raise NotImplementedError

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Finalize the LangSmith-compatible run."""
        raise NotImplementedError


class ClientFactory(Protocol):
    def __call__(self, *, api_key: str) -> Any:
        """Construct a LangSmith-compatible client."""
        raise NotImplementedError


class TraceFactory(Protocol):
    def __call__(
        self,
        name: str,
        run_type: RunType,
        *,
        inputs: dict[str, JsonValue],
        project_name: str,
        metadata: dict[str, JsonValue],
        client: Any,
    ) -> AsyncTraceManager:
        """Construct one LangSmith-compatible async trace manager."""
        raise NotImplementedError


MetricFactory: TypeAlias = Callable[
    [TraceContext, float, bool, str | None, str | None, "SpanHandle"],
    MetricRecord,
]


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def populate_or_validate_total(self) -> "TokenUsage":
        expected = self.input_tokens + self.output_tokens
        if self.total_tokens is None:
            self.total_tokens = expected
        elif self.total_tokens != expected:
            raise ValueError(
                "total_tokens must equal input_tokens + output_tokens"
            )
        return self


@dataclass(slots=True)
class SpanHandle:
    context: TraceContext
    trace_url: str | None = None
    outputs: dict[str, JsonValue] | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    def set_outputs(self, outputs: Mapping[str, JsonValue]) -> None:
        self.outputs = dict(outputs)

    def set_token_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int | None = None,
    ) -> None:
        self.token_usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


class Tracker:
    def __init__(
        self,
        runtime: LangSmithRuntimeConfig,
        *,
        client_factory: ClientFactory = Client,
        trace_factory: TraceFactory = trace,
    ) -> None:
        self._runtime = runtime
        self._trace_factory = trace_factory
        self._client: Any | None = None
        self._events: list[ResearchEvent] = []
        self._errors: list[ResearchError] = []
        self._metrics: list[MetricRecord] = []
        if runtime.tracing_enabled:
            assert runtime.api_key is not None
            try:
                self._client = client_factory(
                    api_key=runtime.api_key.get_secret_value(),
                )
            except Exception as error:
                self._record_langsmith_failure(
                    stage="client_create",
                    context=None,
                    error=error,
                )

    @classmethod
    def from_config(
        cls,
        config: LangSmithConfig,
        *,
        environ: Mapping[str, str] | None = None,
        client_factory: ClientFactory = Client,
        trace_factory: TraceFactory = trace,
    ) -> "Tracker":
        runtime = load_langsmith_runtime_config(config, environ=environ)
        return cls(
            runtime,
            client_factory=client_factory,
            trace_factory=trace_factory,
        )

    @property
    def events(self) -> Sequence[ResearchEvent]:
        return tuple(self._events)

    @property
    def errors(self) -> Sequence[ResearchError]:
        return tuple(self._errors)

    @property
    def metrics(self) -> Sequence[MetricRecord]:
        return tuple(self._metrics)
```

Continue the same file with the public wrappers. Each wrapper validates/derives context, then delegates to `_span(kind: SpanKind, name: str, run_type: RunType, context: TraceContext, inputs: Mapping[str, JsonValue], metric_factory: MetricFactory)`:

```python
    def session_span(
        self,
        session_id: str,
        question: str,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        context = TraceContext(session_id=session_id)
        return self._span(
            kind="session",
            name="research.session",
            run_type="chain",
            context=context,
            inputs={"question": question},
            metric_factory=lambda ctx, latency, success, error_type, trace_url, handle: SessionMetric(
                session_id=ctx.session_id,
                latency_ms=latency,
                success=success,
                error_type=error_type,
                trace_url=trace_url,
            ),
        )

    def agent_span(self, agent_name: str) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        context = parent.model_copy(
            update={"agent_name": agent_name, "tool_name": None, "model": None}
        )
        return self._span(
            kind="agent",
            name=f"agent.{agent_name}",
            run_type="chain",
            context=context,
            inputs={},
            metric_factory=lambda ctx, latency, success, error_type, trace_url, handle: AgentMetric(
                session_id=ctx.session_id,
                agent_name=agent_name,
                scope="agent",
                latency_ms=latency,
                success=success,
                error_type=error_type,
            ),
        )

    def react_iteration_span(self, iteration: int) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        if parent.agent_name is None:
            raise RuntimeError("ReAct iteration spans require an active agent span")
        context = parent.model_copy(
            update={"iteration": iteration, "tool_name": None, "model": None}
        )
        return self._span(
            kind="react_iteration",
            name=f"react.iteration.{iteration}",
            run_type="chain",
            context=context,
            inputs={"iteration": iteration},
            metric_factory=lambda ctx, latency, success, error_type, trace_url, handle: AgentMetric(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name or "unknown",
                scope="react_iteration",
                iteration=iteration,
                latency_ms=latency,
                success=success,
                error_type=error_type,
            ),
        )

    def llm_span(
        self,
        model: str,
        inputs: Mapping[str, JsonValue],
    ) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        context = parent.model_copy(update={"model": model, "tool_name": None})
        return self._span(
            kind="llm",
            name=f"llm.{model}",
            run_type="llm",
            context=context,
            inputs=inputs,
            metric_factory=lambda ctx, latency, success, error_type, trace_url, handle: TokenUsageMetric(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name,
                model=model,
                input_tokens=handle.token_usage.input_tokens,
                output_tokens=handle.token_usage.output_tokens,
                total_tokens=handle.token_usage.total_tokens or 0,
                latency_ms=latency,
                success=success,
                error_type=error_type,
            ),
        )

    def tool_span(
        self,
        tool_name: str,
        inputs: Mapping[str, JsonValue],
        *,
        retry_count: int = 0,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        context = parent.model_copy(update={"tool_name": tool_name, "model": None})
        return self._span(
            kind="tool",
            name=f"tool.{tool_name}",
            run_type="tool",
            context=context,
            inputs=inputs,
            metric_factory=lambda ctx, latency, success, error_type, trace_url, handle: ToolMetric(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name,
                tool_name=tool_name,
                iteration=ctx.iteration,
                retry_count=retry_count,
                latency_ms=latency,
                success=success,
                error_type=error_type,
            ),
        )

    def _require_context(self) -> TraceContext:
        context = current_trace_context()
        if context is None:
            raise RuntimeError("child spans require an active session span")
        return context
```

Implement `_span(...)` first as a local-only async context manager. It must:

1. append an `observability.span.started` event;
2. bind the supplied `TraceContext`;
3. yield `SpanHandle`;
4. on operation exceptions, capture the exception type, append a failed metric/event, and re-raise;
5. on success, append a successful metric/event;
6. compute `latency_ms = (perf_counter() - started_at) * 1000`;
7. include `token_usage` in LLM completion metadata and `trace_url` when present.

Implement the local engine and helpers with the exact signatures below. The bodies are deliberately explicit so Task 4 can add LangSmith behavior without changing the public API:

```python
    @asynccontextmanager
    async def _span(
        self,
        *,
        kind: SpanKind,
        name: str,
        run_type: RunType,
        context: TraceContext,
        inputs: Mapping[str, JsonValue],
        metric_factory: MetricFactory,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        del run_type, inputs
        started_at = perf_counter()
        handle = SpanHandle(context=context)
        operation_error: BaseException | None = None
        self._record_span_event(
            phase="started",
            kind=kind,
            name=name,
            context=context,
        )
        try:
            with bind_trace_context(context):
                try:
                    yield handle
                except BaseException as error:
                    operation_error = error
                    raise
        finally:
            latency_ms = (perf_counter() - started_at) * 1000
            success = operation_error is None
            error_type = (
                type(operation_error).__name__
                if operation_error is not None
                else None
            )
            self._metrics.append(
                metric_factory(
                    context,
                    latency_ms,
                    success,
                    error_type,
                    None,
                    handle,
                )
            )
            self._record_span_event(
                phase="completed",
                kind=kind,
                name=name,
                context=context,
                latency_ms=latency_ms,
                success=success,
                error_type=error_type,
                token_usage=(handle.token_usage if kind == "llm" else None),
            )

    def _record_span_event(
        self,
        *,
        phase: Literal["started", "completed"],
        kind: SpanKind,
        name: str,
        context: TraceContext,
        latency_ms: float | None = None,
        success: bool | None = None,
        error_type: str | None = None,
        token_usage: TokenUsage | None = None,
        trace_url: str | None = None,
    ) -> None:
        metadata: dict[str, JsonValue] = build_trace_metadata(
            context,
            extra={"span_kind": kind, "span_name": name},
        )
        if latency_ms is not None:
            metadata["latency_ms"] = latency_ms
        if success is not None:
            metadata["success"] = success
        if error_type is not None:
            metadata["error_type"] = error_type
        if token_usage is not None:
            metadata["token_usage"] = token_usage.model_dump()
        if trace_url is not None:
            metadata["trace_url"] = trace_url
        self._events.append(
            ResearchEvent(
                event_type=f"observability.span.{phase}",
                source="observability",
                message=f"{name} {phase}.",
                metadata=metadata,
            )
        )

    def _record_langsmith_failure(
        self,
        *,
        stage: str,
        context: TraceContext | None,
        error: Exception,
    ) -> None:
        details: dict[str, JsonValue] = {
            "stage": stage,
            "exception_type": type(error).__name__,
        }
        if context is not None:
            details["session_id"] = context.session_id
        self._errors.append(
            ResearchError(
                error_type="langsmith_tracing_failure",
                source="langsmith",
                message=(
                    f"LangSmith tracing failed during {stage}; "
                    "continuing locally."
                ),
                details=details,
            )
        )
```

`_record_langsmith_failure(stage: str, context: TraceContext | None, error: Exception) -> None` appends a recoverable `ResearchError(error_type="langsmith_tracing_failure", source="langsmith", message="LangSmith tracing failed during {stage}; continuing locally.", details={"stage": stage, "exception_type": type(error).__name__, "session_id": context.session_id if context else None})`, omitting null detail values.

- [ ] **Step 4: Run disabled-mode tracker tests**

Run:

```bash
python -m pip install -e ".[dev]"
python -m pytest tests/test_observability_tracker.py -v
```

Expected: the three disabled-mode tests pass; no LangSmith collaborator is called; operation failures still propagate.

- [ ] **Step 5: Commit the local tracker API**

```bash
git add pyproject.toml src/deep_research/observability/tracker.py tests/test_observability_tracker.py
git commit -m "feat: add local observability tracker"
```


### Task 4: Enabled LangSmith Emission And Failure Fallback

**Files:**
- Modify: `src/deep_research/observability/tracker.py`
- Modify: `tests/test_observability_tracker.py`

**Interfaces:**
- Consumes: the public tracker contracts and helper signatures from Task 3 plus LangSmith-compatible `client_factory(api_key: str) -> object` and `trace_factory(name: str, run_type: str, *, inputs: dict[str, JsonValue], project_name: str, metadata: dict[str, JsonValue], client: object) -> AsyncTraceManager` collaborators.
- Produces: enabled tracing with nested LangSmith runs, session trace URL capture, remote completion metadata, and operation-local no-op fallback after any LangSmith failure.

- [ ] **Step 1: Add fake LangSmith collaborators and failing enabled-mode tests**

Append to `tests/test_observability_tracker.py`:

```python
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
```

- [ ] **Step 2: Run enabled-mode tests and verify failure**

Run:

```bash
python -m pytest tests/test_observability_tracker.py -v
```

Expected: disabled-mode tests pass, while enabled-mode assertions fail because `_span(...)` does not yet enter the LangSmith trace manager or capture a trace URL.

- [ ] **Step 3: Add safe LangSmith entry, completion, and exit helpers**

Add these private helpers to `Tracker`:

```python
    async def _enter_remote_span(
        self,
        *,
        kind: SpanKind,
        name: str,
        run_type: RunType,
        context: TraceContext,
        inputs: Mapping[str, JsonValue],
    ) -> tuple[AsyncTraceManager | None, RunLike | None, str | None]:
        if self._client is None:
            return None, None, None

        try:
            manager = self._trace_factory(
                name,
                run_type,
                inputs=dict(inputs),
                project_name=self._runtime.project,
                metadata=build_trace_metadata(
                    context,
                    extra={"span_kind": kind},
                ),
                client=self._client,
            )
            run = await manager.__aenter__()
        except Exception as error:
            self._record_langsmith_failure(
                stage="span_enter",
                context=context,
                error=error,
            )
            return None, None, None

        trace_url = None
        if kind == "session":
            try:
                trace_url = run.get_url()
            except Exception as error:
                self._record_langsmith_failure(
                    stage="trace_url",
                    context=context,
                    error=error,
                )
        return manager, run, trace_url

    def _end_remote_run(
        self,
        *,
        run: RunLike | None,
        handle: SpanHandle,
        context: TraceContext,
        latency_ms: float,
        success: bool,
        error: BaseException | None,
    ) -> None:
        if run is None:
            return

        completion_metadata: dict[str, Any] = {
            "latency_ms": latency_ms,
            "success": success,
            "error_type": type(error).__name__ if error is not None else None,
            "trace_url": handle.trace_url,
        }
        if context.model is not None:
            completion_metadata["model"] = context.model
            completion_metadata["token_usage"] = handle.token_usage.model_dump()
        completion_metadata = {
            key: value
            for key, value in completion_metadata.items()
            if value is not None
        }

        try:
            run.end(
                outputs=handle.outputs,
                error=str(error) if error is not None else None,
                metadata=completion_metadata,
            )
        except Exception as langsmith_error:
            self._record_langsmith_failure(
                stage="span_end",
                context=context,
                error=langsmith_error,
            )

    async def _exit_remote_span(
        self,
        *,
        manager: AsyncTraceManager | None,
        context: TraceContext,
        operation_error: BaseException | None,
    ) -> None:
        if manager is None:
            return

        try:
            await manager.__aexit__(
                type(operation_error) if operation_error is not None else None,
                operation_error,
                operation_error.__traceback__
                if operation_error is not None
                else None,
            )
        except Exception as error:
            self._record_langsmith_failure(
                stage="span_exit",
                context=context,
                error=error,
            )
```

Remove the accidental leading `+` characters after pasting.

- [ ] **Step 4: Integrate remote tracing into `_span(...)` without swallowing work errors**

Replace the local-only `_span(...)` body with this control flow:

```python
    @asynccontextmanager
    async def _span(
        self,
        *,
        kind: SpanKind,
        name: str,
        run_type: RunType,
        context: TraceContext,
        inputs: Mapping[str, JsonValue],
        metric_factory: MetricFactory,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        started_at = perf_counter()
        handle = SpanHandle(context=context)
        operation_error: BaseException | None = None
        manager, run, trace_url = await self._enter_remote_span(
            kind=kind,
            name=name,
            run_type=run_type,
            context=context,
            inputs=inputs,
        )
        handle.trace_url = trace_url
        bound_context = context.model_copy(update={"trace_url": trace_url})
        handle.context = bound_context
        self._record_span_event(
            phase="started",
            kind=kind,
            name=name,
            context=bound_context,
            trace_url=trace_url,
        )

        try:
            with bind_trace_context(bound_context):
                try:
                    yield handle
                except BaseException as error:
                    operation_error = error
                    raise
        finally:
            latency_ms = (perf_counter() - started_at) * 1000
            success = operation_error is None
            error_type = (
                type(operation_error).__name__
                if operation_error is not None
                else None
            )
            self._end_remote_run(
                run=run,
                handle=handle,
                context=bound_context,
                latency_ms=latency_ms,
                success=success,
                error=operation_error,
            )
            await self._exit_remote_span(
                manager=manager,
                context=bound_context,
                operation_error=operation_error,
            )
            self._metrics.append(
                metric_factory(
                    bound_context,
                    latency_ms,
                    success,
                    error_type,
                    trace_url,
                    handle,
                )
            )
            self._record_span_event(
                phase="completed",
                kind=kind,
                name=name,
                context=bound_context,
                latency_ms=latency_ms,
                success=success,
                error_type=error_type,
                token_usage=(
                    handle.token_usage if kind == "llm" else None
                ),
                trace_url=trace_url,
            )
```

This ordering preserves the operation exception because all LangSmith failures are caught internally before Python resumes propagation of `operation_error`.

- [ ] **Step 5: Run tracker tests and inspect call payloads**

Run:

```bash
python -m pytest tests/test_observability_tracker.py -v
```

Expected: enabled path, disabled path, client failure, span-entry fallback, span-exit fallback, nested context, metric capture, and operation error propagation all pass.

- [ ] **Step 6: Commit enabled tracing and fallback**

```bash
git add src/deep_research/observability/tracker.py tests/test_observability_tracker.py
git commit -m "feat: emit resilient LangSmith traces"
```


### Task 5: Public Exports, Documentation, And Full Verification

**Files:**
- Modify: `src/deep_research/observability/__init__.py:1`
- Modify: `tests/test_imports.py:1`
- Modify: `.env.example:1`
- Modify: `README.md:23`

**Interfaces:**
- Consumes: all public observability contracts completed in Tasks 1-4.
- Produces: the supported `deep_research.observability` import surface and user-facing configuration/usage documentation.

- [ ] **Step 1: Write failing public import test**

Append to `tests/test_imports.py`:

```python
def test_observability_contracts_import_from_package() -> None:
    from deep_research.observability import (  # noqa: F401
        AgentMetric,
        LangSmithRuntimeConfig,
        MetricRecord,
        SessionMetric,
        SpanHandle,
        TokenUsage,
        TokenUsageMetric,
        ToolMetric,
        TraceContext,
        Tracker,
        bind_trace_context,
        build_trace_metadata,
        current_trace_context,
        load_langsmith_runtime_config,
    )
```

- [ ] **Step 2: Run import test and verify failure**

Run:

```bash
python -m pytest tests/test_imports.py::test_observability_contracts_import_from_package -v
```

Expected: FAIL with `ImportError` because `deep_research.observability.__init__` is still a stub.

- [ ] **Step 3: Export the stable observability API**

Replace `src/deep_research/observability/__init__.py` with:

```python
"""LangSmith-backed observability contracts with local fallback."""

from deep_research.observability.context import (
    LangSmithRuntimeConfig,
    TraceContext,
    bind_trace_context,
    build_trace_metadata,
    current_trace_context,
    load_langsmith_runtime_config,
)
from deep_research.observability.metrics import (
    AgentMetric,
    MetricRecord,
    SessionMetric,
    TokenUsageMetric,
    ToolMetric,
)
from deep_research.observability.tracker import SpanHandle, TokenUsage, Tracker

__all__ = [
    "AgentMetric",
    "LangSmithRuntimeConfig",
    "MetricRecord",
    "SessionMetric",
    "SpanHandle",
    "TokenUsage",
    "TokenUsageMetric",
    "ToolMetric",
    "TraceContext",
    "Tracker",
    "bind_trace_context",
    "build_trace_metadata",
    "current_trace_context",
    "load_langsmith_runtime_config",
]
```

- [ ] **Step 4: Document enabled and disabled configuration**

Replace the LangSmith portion of `.env.example` with:

```dotenv
# Optional: true enables LangSmith emission; false keeps local events/metrics only.
LANGSMITH_TRACING=false

# Required only when LANGSMITH_TRACING=true.
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=
```

Keep the existing OpenAI and Tavily entries. Update the first comment so it reads:

```dotenv
# Provider values are required when strict mode is enabled. LangSmith values are
# additionally required whenever LANGSMITH_TRACING=true.
```

In `README.md`, update `Project Status` to:

```markdown
Foundation phase — package skeleton, typed configuration/state, and the LangSmith observability foundation.
```

After the Configuration section, add:

```markdown
## Observability

Set `LANGSMITH_TRACING=false` to keep tracing fully local for tests and offline
development. Set it to `true` and provide non-empty `LANGSMITH_API_KEY` and
`LANGSMITH_PROJECT` values to mirror the same spans to LangSmith.

Application code imports only the project tracker:

```python
from deep_research.observability import Tracker
from deep_research.utils.config import load_config

settings = load_config("config.yaml")
tracker = Tracker.from_config(settings.langsmith)

async with tracker.session_span("session-123", "Why is the sky blue?"):
    async with tracker.agent_span("planner"):
        async with tracker.tool_span(
            "web_search",
            {"query": "Rayleigh scattering"},
        ):
            results = await search_client.search("Rayleigh scattering")
```

The documentation snippet uses a named `search_client` placeholder only as an example dependency; the observability implementation does not create that client.

Each completed span appends a typed metric and structured event. A LangSmith
transport failure appends a recoverable `ResearchError` and research work
continues locally.
```

- [ ] **Step 5: Run focused import and observability tests**

Run:

```bash
python -m pytest tests/test_imports.py tests/test_observability_context.py tests/test_observability_metrics.py tests/test_observability_tracker.py -v
```

Expected: all observability and import tests pass.

- [ ] **Step 6: Run full verification**

Run:

```bash
python -m pytest -v
ruff check src tests
git diff --check
```

Expected: the complete test suite passes, Ruff prints `All checks passed!`, and `git diff --check` prints no output.

- [ ] **Step 7: Commit exports and documentation**

```bash
git add src/deep_research/observability/__init__.py tests/test_imports.py .env.example README.md
git commit -m "docs: publish observability API"
```

- [ ] **Step 8: Verify the branch is clean**

Run:

```bash
git status --short
```

Expected: no output.
