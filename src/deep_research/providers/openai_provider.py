"""OpenAI chat and embedding providers with project-owned contracts.

The ``openai`` SDK is imported lazily (see ``_openai_errors``) so importing
this module, and therefore ``deep_research.providers``, does not require the
package to be installed at collection time.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, TypeVar

from pydantic import BaseModel, JsonValue, ValidationError

from deep_research.observability import TokenUsage, Tracker
from deep_research.providers.capabilities import resolve_request_settings
from deep_research.providers.contracts import (
    ChatMessage,
    ChatResult,
    NativeToolCall,
    NativeToolTurn,
    OpenAIProviderError,
    ProviderConfigurationError,
    ProviderError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
    ToolDefinition,
)
from deep_research.providers.retry import with_retries
from deep_research.providers.validation import (
    validation_diagnostic,
    validation_diagnostic_from_text,
    validation_summary,
)
from deep_research.utils.config import EffectiveModelConfig, LLMConfig

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _resolve_max_tokens(global_cap: int, override: int | None) -> int:
    """Resolve one request's output budget: the global cap unless overridden.

    A per-call override applies to that request field only and must be a
    positive integer; anything else is rejected before the SDK is touched.
    """
    if override is None:
        return global_cap
    if override < 1:
        raise ValueError("max_tokens must be a positive integer when provided")
    return override

_openai_sdk: SimpleNamespace | None = None


def _openai_errors() -> SimpleNamespace:
    """Import the openai SDK on first use and cache the symbols we need."""
    global _openai_sdk
    if _openai_sdk is None:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AsyncOpenAI,
            OpenAIError,
            RateLimitError,
        )

        _openai_sdk = SimpleNamespace(
            APIConnectionError=APIConnectionError,
            APIStatusError=APIStatusError,
            APITimeoutError=APITimeoutError,
            AsyncOpenAI=AsyncOpenAI,
            OpenAIError=OpenAIError,
            RateLimitError=RateLimitError,
        )
    return _openai_sdk


def _build_client(
    config: LLMConfig,
    *,
    api_key: str | None,
    client: Any | None,
) -> Any:
    if client is not None:
        return client
    resolved_key = os.getenv("OPENAI_API_KEY", "") if api_key is None else api_key
    if not resolved_key.strip():
        raise ProviderConfigurationError(
            "OPENAI_API_KEY is required when no OpenAI client is injected"
        )
    return _openai_errors().AsyncOpenAI(
        api_key=resolved_key,
        timeout=config.timeout,
        # SDK retries are disabled: the repo-owned retry policy in
        # providers/retry.py owns the retry count and backoff.
        max_retries=0,
    )


def _usage_from_response(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    input_tokens = getattr(usage, "input_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "output_tokens", 0)
    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or input_tokens < 0
        or isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        raise ProviderResponseError(
     "OpenAI response contained malformed usage",
     failure_origin="local_response",
 )
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _set_span_result(span: Any, response: Any, usage: TokenUsage) -> None:
    span.set_outputs(
        {
            "provider": "openai",
            "response_id": getattr(response, "id", "unknown"),
            "model_returned": getattr(response, "model", None),
        }
    )
    span.set_token_usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


def _raise_provider_error(error: Exception) -> None:
    sdk = _openai_errors()
    if isinstance(error, sdk.APITimeoutError):
        raise ProviderTimeoutError("OpenAI request timed out") from error
    if isinstance(error, sdk.RateLimitError):
        raise ProviderRateLimitError("OpenAI rate limit exceeded") from error
    if isinstance(error, sdk.APIConnectionError):
        raise ProviderResponseError(
            "OpenAI connection failed",
            failure_origin="sdk",
            retryable=True,
            failure_category="transport",
        ) from error
    if isinstance(error, sdk.APIStatusError):
        status = error.status_code
        raise ProviderResponseError(
            f"OpenAI request failed with status {status}",
            failure_origin="sdk",
            retryable=status >= 500 or status in (408, 409),
            failure_category="http",
            http_status_code=status,
        ) from error
    raise error


def _fresh_provider_error(error: ProviderResponseError) -> ProviderResponseError:
    """A copy of a typed rejection carrying no traceback and no chain.

    Re-raising the caught object would keep its original traceback, whose
    frames still reference the raw response. A new instance carries only the
    static project-authored message.
    """
    return ProviderResponseError(
        str(error),
        failure_origin=error.failure_origin,
        retryable=error.retryable,
        failure_category=error.failure_category,
        http_status_code=error.http_status_code,
    )


def _native_response_outcome(
    response: Any,
    *,
    allowed: set[str],
    usage: TokenUsage,
    configured_max_tokens: int,
    request_attempt: int,
) -> tuple[NativeToolCall | None, str | None, ProviderError | None]:
    """Read exactly one Responses function call or one non-blank final answer.

    Returns ``(tool_call, final_answer, failure)`` with at most one set. This
    never raises, so the caller can clear its own provider-adjacent locals
    before a rejection becomes a public error whose traceback would otherwise
    retain the raw response.

    Reasoning items are stepped over by type and never read, retained, or
    traced. Only a typed ``function_call`` item can select a tool, so tool
    markup in ordinary text cannot request execution.
    """
    status = getattr(response, "status", None)
    if status == "incomplete":
        incomplete = getattr(response, "incomplete_details", None)
        reason = (
            getattr(incomplete, "reason", None) if incomplete is not None else None
        )
        if reason == "max_output_tokens":
            return (
                None,
                None,
                ProviderOutputLimitError(
                    ProviderResponseTelemetry(
                        finish_reason_category="length",
                        configured_max_tokens=configured_max_tokens,
                        usage=usage,
                        request_attempt=request_attempt,
                    )
                ),
            )
        return None, None, ProviderResponseError(
            "OpenAI response did not complete",
            failure_origin="local_response",
        )
    if status != "completed":
        return None, None, ProviderResponseError(
            "OpenAI response did not complete",
            failure_origin="local_response",
        )

    output = getattr(response, "output", None)
    if output is None:
        items: Sequence[Any] = ()
    elif isinstance(output, (list, tuple)):
        items = output
    else:
        return None, None, ProviderResponseError(
            "OpenAI response contained malformed output",
            failure_origin="local_response",
        )
    calls = [item for item in items if getattr(item, "type", None) == "function_call"]
    output_text = getattr(response, "output_text", None)
    text = output_text.strip() if isinstance(output_text, str) else ""

    if calls:
        if text:
            return None, None, ProviderResponseError(
                "OpenAI native tool response mixed a final answer with a tool call",
                failure_origin="local_response",
            )
        if len(calls) != 1:
            return None, None, ProviderResponseError(
                "OpenAI native tool response must carry exactly one tool call",
                failure_origin="local_response",
            )
        call = calls[0]
        name = getattr(call, "name", None)
        if not isinstance(name, str) or name not in allowed:
            return None, None, ProviderResponseError(
                "OpenAI native tool response named an unavailable tool",
                failure_origin="local_response",
            )
        arguments = getattr(call, "arguments", None)
        if not isinstance(arguments, str) or not arguments.strip():
            return None, None, ProviderResponseError(
                "OpenAI native tool response carried malformed arguments",
                failure_origin="local_response",
            )
        return NativeToolCall(tool_name=name, arguments_json=arguments), None, None

    if not text:
        return None, None, ProviderResponseError(
            "OpenAI native tool response carried no usable final answer",
            failure_origin="local_response",
        )
    return None, text, None


class _StructuredValidationFailure(RuntimeError):
    def __init__(
        self,
        schema_name: str,
        diagnostic: StructuredValidationDiagnostic,
    ) -> None:
        super().__init__(f"OpenAI output failed {schema_name} validation")
        self.diagnostic = diagnostic


class OpenAIChatProvider:
    """Async text and structured-output access through OpenAI Responses."""

    def __init__(
        self,
        config: LLMConfig,
        tracker: Tracker,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._tracker = tracker
        self._client = _build_client(config, api_key=api_key, client=client)
        self._last_model_returned: str | None = None

    @property
    def last_model_returned(self) -> str | None:
        """The model identifier the last successful response reported.

        The evaluation harness records the requested alias *and* what the
        provider actually served, because ``gpt-5.6-luna`` is an alias with
        no dated snapshot.
        """
        return self._last_model_returned

    def _request_options(
        self, agent_name: str | None
    ) -> tuple[EffectiveModelConfig, dict[str, object], dict[str, JsonValue]]:
        """Resolve and validate request settings for one effective model.

        The capability registry runs before any client call, so an
        unsupported model, thinking mode, or effort raises before the SDK
        is touched. Span metadata carries only model-span facts; message
        content never appears.
        """
        effective = self._config.resolve_for(agent_name)
        resolved = resolve_request_settings("openai", effective)
        request: dict[str, object] = {
            "model": effective.model,
            "max_output_tokens": self._config.max_tokens,
        }
        if resolved.reasoning_effort is not None:
            request["reasoning"] = {"effort": resolved.reasoning_effort}
        if resolved.include_temperature:
            request["temperature"] = self._config.temperature
        metadata: dict[str, JsonValue] = {
            "provider": "openai",
            "thinking_mode": effective.thinking_mode,
            "requested_reasoning_effort": effective.reasoning_effort,
        }
        if agent_name is not None:
            metadata["agent_name"] = agent_name
        if resolved.reasoning_effort is not None:
            metadata["effective_reasoning_effort"] = resolved.reasoning_effort
        return effective, request, metadata

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        agent_name: str | None = None,
    ) -> ChatResult:
        if not messages:
            raise ValueError("messages must contain at least one item")
        effective, request, metadata = self._request_options(agent_name)
        payload = [message.model_dump(mode="json") for message in messages]
        try:
            async with self._tracker.llm_span(
                effective.model,
                {
                    **metadata,
                    "operation": "chat",
                    "message_count": len(payload),
                },
            ) as span:
                _sdk = _openai_errors()

                async def _request() -> Any:
                    try:
                        return await self._client.responses.create(
                            **{**request, "input": payload}
                        )
                    except (
                        _sdk.APITimeoutError,
                        _sdk.RateLimitError,
                        _sdk.APIConnectionError,
                        _sdk.APIStatusError,
                    ) as error:
                        _raise_provider_error(error)
                    except _sdk.OpenAIError as error:
                        raise ProviderResponseError(
                            "OpenAI chat request failed",
                            failure_origin="sdk",
                        ) from error

                response = await with_retries(
                    _request,
                    retry_count=self._config.retry_count,
                    initial_delay=self._config.retry_initial_delay,
                    max_delay=self._config.retry_max_delay,
                )
                output_text = getattr(response, "output_text", None)
                if not isinstance(output_text, str):
                    raise ProviderResponseError(
                        "OpenAI response did not contain text output",
                        failure_origin="local_response",
                    )
                text = output_text.strip()
                if not text:
                    raise ProviderResponseError(
                        "OpenAI response did not contain text output",
                        failure_origin="local_response",
                    )
                usage = _usage_from_response(response)
                _set_span_result(span, response, usage)
                self._last_model_returned = (
                    getattr(response, "model", None) or effective.model
                )
                return ChatResult(text=text, model=effective.model, usage=usage)
        except OpenAIProviderError:
            raise

    async def _structured_attempt(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        model: str,
        request: dict[str, object],
        metadata: dict[str, JsonValue],
        attempt: int,
    ) -> SchemaT:
        payload = [message.model_dump(mode="json") for message in messages]
        async with self._tracker.llm_span(
            model,
            {
                **metadata,
                "operation": "structured_output",
                "attempt": attempt,
                "message_count": len(payload),
            },
        ) as span:
            _sdk = _openai_errors()

            async def _request() -> Any:
                try:
                    return await self._client.responses.parse(
                        **{**request, "input": payload, "text_format": schema}
                    )
                except (
                    _sdk.APITimeoutError,
                    _sdk.RateLimitError,
                    _sdk.APIConnectionError,
                    _sdk.APIStatusError,
                ) as error:
                    _raise_provider_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "OpenAI structured output request failed",
                        failure_origin="sdk",
                    ) from error
                except ValidationError as error:
                    raise _StructuredValidationFailure(
                        schema.__name__,
                        validation_diagnostic(
                            error, attempt=attempt, schema=schema
                        ),
                    ) from None

            response = await with_retries(
                _request,
                retry_count=self._config.retry_count,
                initial_delay=self._config.retry_initial_delay,
                max_delay=self._config.retry_max_delay,
            )
            usage = _usage_from_response(response)
            _set_span_result(span, response, usage)
            parsed = getattr(response, "output_parsed", None)
            if not isinstance(parsed, schema):
                diagnostic = validation_diagnostic_from_text(
                    getattr(response, "output_text", None),
                    attempt=attempt,
                    schema=schema,
                )
                raise _StructuredValidationFailure(
                    schema.__name__, diagnostic
                ) from None
            self._last_model_returned = getattr(response, "model", None) or model
            return parsed

    async def complete_react(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> NativeToolTurn:
        """One native ReAct turn: a provider tool call or a final answer.

        Offline parity with the DeepSeek boundary, on Responses' native
        function-tool representation. Reasoning output items are ignored and
        never retained; only a typed ``function_call`` item can select a tool.
        There is no structured output and no repair here.
        """
        if not messages:
            raise ValueError("messages must contain at least one item")
        if not tools:
            raise ValueError("tools must contain at least one item")
        resolved_max_tokens = _resolve_max_tokens(
            self._config.max_tokens, max_tokens
        )
        effective, request, metadata = self._request_options(agent_name)
        request = {**request, "max_output_tokens": resolved_max_tokens}
        payload = [message.model_dump(mode="json") for message in messages]
        allowed = {definition.name for definition in tools}
        request_attempt = 0
        async with self._tracker.llm_span(
            effective.model,
            {
                **metadata,
                "operation": "react_tool_turn",
                "message_count": len(payload),
                "tool_count": len(tools),
            },
        ) as span:
            _sdk = _openai_errors()

            async def _request() -> Any:
                nonlocal request_attempt
                request_attempt += 1
                try:
                    return await self._client.responses.create(
                        **{
                            **request,
                            "input": payload,
                            "tools": [
                                {
                                    "type": "function",
                                    "name": definition.name,
                                    "description": definition.description,
                                    "parameters": definition.parameters,
                                }
                                for definition in tools
                            ],
                            "tool_choice": "auto",
                        }
                    )
                except (
                    _sdk.APITimeoutError,
                    _sdk.RateLimitError,
                    _sdk.APIConnectionError,
                    _sdk.APIStatusError,
                ) as error:
                    _raise_provider_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "OpenAI native tool request failed",
                        failure_origin="sdk",
                    ) from error

            response = await with_retries(
                _request,
                retry_count=self._config.retry_count,
                initial_delay=self._config.retry_initial_delay,
                max_delay=self._config.retry_max_delay,
            )
            usage: TokenUsage | None = None
            tool_call: NativeToolCall | None = None
            final_answer: str | None = None
            failure: ProviderError | None = None
            try:
                usage = _usage_from_response(response)
            except ProviderResponseError as error:
                # A malformed usage shape is rejected *before* the clearing
                # block below, so the rejection has to be replaced with a
                # traceback-free copy: re-raising the caught object would keep
                # the frames that still hold the raw response.
                failure = _fresh_provider_error(error)
            if failure is None:
                _set_span_result(span, response, usage)
                tool_call, final_answer, failure = _native_response_outcome(
                    response,
                    allowed=allowed,
                    usage=usage,
                    configured_max_tokens=resolved_max_tokens,
                    request_attempt=request_attempt,
                )
            if failure is None:
                self._last_model_returned = (
                    getattr(response, "model", None) or effective.model
                )
                return NativeToolTurn(
                    model=effective.model,
                    usage=usage,
                    tool_call=tool_call,
                    final_answer=final_answer,
                )

            # Do not raise while holding provider-adjacent locals: the public
            # error's traceback would otherwise retain the raw response,
            # including reasoning items. ``complete_structured`` clears its
            # locals for the same reason.
            response = None
            payload = []
            request = {}
            metadata = {}
            messages = ()
            tools = ()
            allowed = set()
            effective = None
            agent_name = None
            usage = TokenUsage()
            tool_call = None
            final_answer = None
            raise failure

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> SchemaT:
        if not messages:
            raise ValueError("messages must contain at least one item")
        resolved_max_tokens = _resolve_max_tokens(
            self._config.max_tokens, max_tokens
        )
        effective, request, metadata = self._request_options(agent_name)
        request = {**request, "max_output_tokens": resolved_max_tokens}
        current_messages = list(messages)

        diagnostics: list[StructuredValidationDiagnostic] = []
        final_error: StructuredOutputError | None = None
        for attempt in (1, 2):
            try:
                return await self._structured_attempt(
                    current_messages,
                    schema,
                    model=effective.model,
                    request=request,
                    metadata=metadata,
                    attempt=attempt,
                )
            except _StructuredValidationFailure as error:
                diagnostics.append(error.diagnostic)
                if attempt == 2:
                    final_error = StructuredOutputError(
                        f"OpenAI output failed {schema.__name__} validation "
                        "after one repair attempt",
                        diagnostics=tuple(diagnostics),
                    )
                    break
                repair_instruction = (
                    f"The previous response failed {schema.__name__} validation. "
                    "Return a corrected response that matches the supplied schema "
                    "exactly. "
                    f"Validation summary: {validation_summary(error.diagnostic)}"
                )
                current_messages = [
                    *messages,
                    ChatMessage(role="developer", content=repair_instruction),
                ]

        if final_error is None:
            raise AssertionError("structured output attempt loop did not return")

        # Do not raise while handling the internal validation failure: that
        # would retain it through ``__context__``/``__cause__``. Clear all
        # provider-adjacent locals before the public error's traceback is
        # captured, leaving only the bounded typed diagnostics.
        self = None
        messages = ()
        current_messages = []
        request = {}
        metadata = {}
        effective = None
        agent_name = None
        schema = BaseModel
        repair_instruction = ""
        raise final_error
