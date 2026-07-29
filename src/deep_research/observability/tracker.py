"""Public observability API with local recording and LangSmith fallback."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
)
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal, Protocol, TypeAlias

from langsmith import Client, trace, tracing_context
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    TypeAdapter,
    model_validator,
)

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
Redactor: TypeAlias = Callable[[Any], JsonValue]
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "credential",
    }
)
_RETRY_COUNT_ADAPTER = TypeAdapter(NonNegativeInt)


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
    def __call__(
        self,
        *,
        api_key: str,
        tracing_error_callback: Callable[[Exception], None],
        anonymizer: Callable[[dict[str, Any]], dict[str, JsonValue]],
    ) -> Any:
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
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
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
        secret = (
            runtime.api_key.get_secret_value()
            if runtime.api_key is not None
            else None
        )
        self._redact: Redactor = _build_redactor(secret)
        self._events: list[ResearchEvent] = []
        self._errors: list[ResearchError] = []
        self._metrics: list[MetricRecord] = []
        if runtime.tracing_enabled:
            try:
                self._client = client_factory(
                    api_key=secret or "",
                    tracing_error_callback=self._background_transport_failure,
                    anonymizer=self._anonymize,
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

    def session_span(
        self,
        session_id: str,
        question: str,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        context = TraceContext(session_id=session_id)

        def metric_factory(
            ctx: TraceContext,
            latency: float,
            success: bool,
            error_type: str | None,
            trace_url: str | None,
            handle: SpanHandle,
        ) -> MetricRecord:
            del handle
            return SessionMetric(
                session_id=ctx.session_id,
                latency_ms=latency,
                success=success,
                error_type=error_type,
                trace_url=trace_url,
            )

        return self._span(
            kind="session",
            name="research.session",
            run_type="chain",
            context=context,
            inputs={"question": question},
            metric_factory=metric_factory,
        )

    def agent_span(self, agent_name: str) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        context = _validated_context(
            parent, agent_name=agent_name, tool_name=None, model=None
        )

        def metric_factory(
            ctx: TraceContext,
            latency: float,
            success: bool,
            error_type: str | None,
            trace_url: str | None,
            handle: SpanHandle,
        ) -> MetricRecord:
            del trace_url, handle
            return AgentMetric(
                session_id=ctx.session_id,
                agent_name=agent_name,
                scope="agent",
                latency_ms=latency,
                success=success,
                error_type=error_type,
            )

        return self._span(
            kind="agent",
            name=f"agent.{agent_name}",
            run_type="chain",
            context=context,
            inputs={},
            metric_factory=metric_factory,
        )

    def react_iteration_span(
        self,
        iteration: int,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        if parent.agent_name is None:
            raise RuntimeError("ReAct iteration spans require an active agent span")
        context = _validated_context(
            parent, iteration=iteration, tool_name=None, model=None
        )

        def metric_factory(
            ctx: TraceContext,
            latency: float,
            success: bool,
            error_type: str | None,
            trace_url: str | None,
            handle: SpanHandle,
        ) -> MetricRecord:
            del trace_url, handle
            return AgentMetric(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name or "unknown",
                scope="react_iteration",
                iteration=iteration,
                latency_ms=latency,
                success=success,
                error_type=error_type,
            )

        return self._span(
            kind="react_iteration",
            name=f"react.iteration.{iteration}",
            run_type="chain",
            context=context,
            inputs={"iteration": iteration},
            metric_factory=metric_factory,
        )

    def llm_span(
        self,
        model: str,
        inputs: Mapping[str, JsonValue],
    ) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        context = _validated_context(parent, model=model, tool_name=None)

        def metric_factory(
            ctx: TraceContext,
            latency: float,
            success: bool,
            error_type: str | None,
            trace_url: str | None,
            handle: SpanHandle,
        ) -> MetricRecord:
            del trace_url
            return TokenUsageMetric(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name,
                model=model,
                input_tokens=handle.token_usage.input_tokens,
                output_tokens=handle.token_usage.output_tokens,
                total_tokens=handle.token_usage.total_tokens or 0,
                latency_ms=latency,
                success=success,
                error_type=error_type,
            )

        return self._span(
            kind="llm",
            name=f"llm.{model}",
            run_type="llm",
            context=context,
            inputs=inputs,
            metric_factory=metric_factory,
        )

    def tool_span(
        self,
        tool_name: str,
        inputs: Mapping[str, JsonValue],
        *,
        retry_count: int = 0,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        context = _validated_context(parent, tool_name=tool_name, model=None)
        retry_count = _RETRY_COUNT_ADAPTER.validate_python(retry_count)

        def metric_factory(
            ctx: TraceContext,
            latency: float,
            success: bool,
            error_type: str | None,
            trace_url: str | None,
            handle: SpanHandle,
        ) -> MetricRecord:
            del trace_url, handle
            return ToolMetric(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name,
                tool_name=tool_name,
                iteration=ctx.iteration,
                retry_count=retry_count,
                latency_ms=latency,
                success=success,
                error_type=error_type,
            )

        return self._span(
            kind="tool",
            name=f"tool.{tool_name}",
            run_type="tool",
            context=context,
            inputs=inputs,
            metric_factory=metric_factory,
        )

    def _require_context(self) -> TraceContext:
        context = current_trace_context()
        if context is None:
            raise RuntimeError("child spans require an active session span")
        return context

    async def _enter_remote_span(
        self,
        *,
        kind: SpanKind,
        name: str,
        run_type: RunType,
        context: TraceContext,
        inputs: Mapping[str, JsonValue],
    ) -> tuple[
        AbstractContextManager[None] | None,
        AsyncTraceManager | None,
        RunLike | None,
        str | None,
    ]:
        if self._client is None:
            return None, None, None, None

        tracing_manager: AbstractContextManager[None] | None = None
        try:
            tracing_manager = tracing_context(
                enabled=True,
                project_name=self._runtime.project,
                client=self._client,
            )
            tracing_manager.__enter__()
        except Exception as error:
            self._record_langsmith_failure(
                stage="tracing_context_enter",
                context=context,
                error=error,
            )
            return None, None, None, None

        try:
            manager = self._trace_factory(
                _redact_string(self._redact, name),
                run_type,
                inputs=_redact_mapping(self._redact, inputs),
                project_name=self._runtime.project,
                metadata=_redact_mapping(
                    self._redact,
                    build_trace_metadata(
                        context,
                        extra={"span_kind": kind},
                    ),
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
            await self._exit_tracing_context(
                manager=tracing_manager,
                context=context,
                operation_error=None,
            )
            return None, None, None, None

        trace_url = None
        if kind == "session":
            try:
                candidate = run.get_url()
                if not isinstance(candidate, str) or not candidate.strip():
                    raise ValueError("LangSmith trace URL must be a non-empty string")
                trace_url = candidate
            except Exception as error:
                self._record_langsmith_failure(
                    stage="trace_url",
                    context=context,
                    error=error,
                )
        return tracing_manager, manager, run, trace_url

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
                outputs=_redact_optional_mapping(self._redact, handle.outputs),
                error=self._redact(str(error)) if error is not None else None,
                metadata=_redact_mapping(self._redact, completion_metadata),
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

        remote_error = self._redacted_exception(operation_error)
        try:
            await manager.__aexit__(
                type(remote_error) if remote_error is not None else None,
                remote_error,
                None,
            )
        except Exception as error:
            self._record_langsmith_failure(
                stage="span_exit",
                context=context,
                error=error,
            )

    async def _exit_tracing_context(
        self,
        *,
        manager: AbstractContextManager[None] | None,
        context: TraceContext,
        operation_error: BaseException | None,
    ) -> None:
        if manager is None:
            return

        remote_error = self._redacted_exception(operation_error)
        try:
            manager.__exit__(
                type(remote_error) if remote_error is not None else None,
                remote_error,
                None,
            )
        except Exception as error:
            self._record_langsmith_failure(
                stage="tracing_context_exit",
                context=context,
                error=error,
            )

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
        handle = SpanHandle(context=context, trace_url=context.trace_url)
        operation_error: BaseException | None = None
        self._record_span_event(
            phase="started",
            kind=kind,
            name=name,
            context=context,
            trace_url=context.trace_url,
        )
        tracing_manager, manager, run, new_session_url = await self._enter_remote_span(
            kind=kind,
            name=name,
            run_type=run_type,
            context=context,
            inputs=inputs,
        )
        effective_trace_url = new_session_url or context.trace_url
        handle.trace_url = effective_trace_url
        bound_context = _validated_context(context, trace_url=effective_trace_url)
        handle.context = bound_context

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
                type(operation_error).__name__ if operation_error is not None else None
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
            await self._exit_tracing_context(
                manager=tracing_manager,
                context=bound_context,
                operation_error=operation_error,
            )
            try:
                self._metrics.append(
                    metric_factory(
                        bound_context,
                        latency_ms,
                        success,
                        error_type,
                        effective_trace_url,
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
                    token_usage=handle.token_usage if kind == "llm" else None,
                    trace_url=effective_trace_url,
                )
            except Exception as error:
                self._record_local_failure(
                    stage="local_finalization",
                    context=bound_context,
                    error=error,
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

    def _anonymize(self, payload: dict[str, Any]) -> dict[str, JsonValue]:
        return _redact_mapping(self._redact, payload)

    def _redacted_exception(
        self,
        error: BaseException | None,
    ) -> BaseException | None:
        if error is None:
            return None
        redacted = self._redact(str(error))
        try:
            return type(error)(redacted)
        except Exception:
            return RuntimeError(redacted)

    def _background_transport_failure(self, error: Exception) -> None:
        try:
            self._record_langsmith_failure(
                stage="background_transport",
                context=current_trace_context(),
                error=error,
            )
        except BaseException:
            return

    def _record_local_failure(
        self,
        *,
        stage: str,
        context: TraceContext,
        error: Exception,
    ) -> None:
        try:
            self._errors.append(
                ResearchError(
                    error_type="local_observability_failure",
                    source="observability",
                    message=(
                        f"Local observability failed during {stage}; "
                        "research work is unchanged."
                    ),
                    details={
                        "stage": stage,
                        "exception_type": type(error).__name__,
                        "session_id": context.session_id,
                    },
                )
            )
        except BaseException:
            return

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
                    f"LangSmith tracing failed during {stage}; continuing locally."
                ),
                details=details,
            )
        )


def _validated_context(context: TraceContext, **updates: Any) -> TraceContext:
    return TraceContext.model_validate({**context.model_dump(), **updates})


def _build_redactor(secret: str | None) -> Redactor:
    normalized_sensitive_keys = {
        "".join(character for character in key.lower() if character.isalnum())
        for key in _SENSITIVE_KEYS
    }

    def redact(value: Any, *, sensitive: bool = False) -> JsonValue:
        if sensitive:
            return _REDACTED
        if isinstance(value, Mapping):
            return {
                str(key): redact(
                    item,
                    sensitive=(
                        "".join(
                            character
                            for character in str(key).lower()
                            if character.isalnum()
                        )
                        in normalized_sensitive_keys
                    ),
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [redact(item) for item in value]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value.replace(secret, _REDACTED) if secret else value
        return redact(str(value))

    return redact


def _redact_mapping(
    redactor: Redactor,
    value: Mapping[str, Any],
) -> dict[str, JsonValue]:
    redacted = redactor(value)
    if not isinstance(redacted, dict):
        raise TypeError("redacted mapping must remain a mapping")
    return redacted


def _redact_string(redactor: Redactor, value: str) -> str:
    redacted = redactor(value)
    if not isinstance(redacted, str):
        raise TypeError("redacted string must remain a string")
    return redacted


def _redact_optional_mapping(
    redactor: Redactor,
    value: Mapping[str, Any] | None,
) -> dict[str, JsonValue] | None:
    return None if value is None else _redact_mapping(redactor, value)
