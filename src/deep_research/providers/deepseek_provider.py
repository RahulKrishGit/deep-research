"""DeepSeek chat provider with project-owned contracts.

The ``openai`` SDK is imported lazily (see ``_openai_errors``) so importing
this module, and therefore ``deep_research.providers``, does not require the
package to be installed at collection time. Chat Completions calls target the
code-owned DeepSeek base URL with the explicit thinking toggle and
capability-driven reasoning and temperature settings.
"""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Mapping, Sequence
from types import SimpleNamespace, UnionType
from typing import Annotated, Any, TypeVar, Union, cast, get_args, get_origin

from pydantic import BaseModel, JsonValue, ValidationError

from deep_research.observability import TokenUsage, Tracker
from deep_research.providers.capabilities import resolve_request_settings
from deep_research.providers.contracts import (
    ChatMessage,
    ChatResult,
    FinishReasonCategory,
    NativeToolCall,
    NativeToolTurn,
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
from deep_research.providers.validation import validation_category
from deep_research.utils.config import EffectiveModelConfig, LLMConfig

SchemaT = TypeVar("SchemaT", bound=BaseModel)
_MAX_VALIDATION_FIELD_PATHS = 16
_MAX_VALIDATION_SUMMARY_LENGTH = 1000


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

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_FINISH_REASON_CATEGORIES = frozenset(
    {
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "insufficient_system_resource",
    }
)

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
    resolved_key = os.getenv("DEEPSEEK_API_KEY", "") if api_key is None else api_key
    if not resolved_key.strip():
        raise ProviderConfigurationError(
            "DEEPSEEK_API_KEY is required when no DeepSeek client is injected"
        )
    return _openai_errors().AsyncOpenAI(
        api_key=resolved_key,
        base_url=DEEPSEEK_BASE_URL,
        timeout=config.timeout,
        # SDK retries are disabled: the repo-owned retry policy in
        # providers/retry.py owns the retry count and backoff.
        max_retries=0,
    )


def _translated_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
    """Translate caller messages into ordered Chat Completions payload roles.

    Only ``developer`` maps to ``system``; ``system``, ``user``, and
    ``assistant`` pass through unchanged and no message is reordered.
    """
    return [
        {
            "role": "system" if message.role == "developer" else message.role,
            "content": message.content,
        }
        for message in messages
    ]


def _json_instruction(schema: type[BaseModel]) -> ChatMessage:
    """Build the deterministic JSON Schema system instruction for one schema.

    The canonical schema is emitted with sorted keys and no whitespace so
    identical schemas always produce byte-identical instructions.
    """
    schema_json = json.dumps(
        schema.model_json_schema(), sort_keys=True, separators=(",", ":")
    )
    content = (
        "Return only one JSON object that validates against this JSON Schema. "
        "Do not add Markdown or explanatory text. JSON Schema:\n"
        f"{schema_json}"
    )
    return ChatMessage(role="system", content=content)


class _StructuredValidationFailure(RuntimeError):
    """Carry validation diagnostics for the repair prompt only.

    The message names the schema but never the provider output. The typed
    diagnostic contains only bounded locations and a stable category.
    """

    def __init__(
        self,
        schema_name: str,
        diagnostic: StructuredValidationDiagnostic,
    ) -> None:
        super().__init__(f"DeepSeek output failed {schema_name} validation")
        self.diagnostic = diagnostic


def _single_schema_annotation(annotation: object) -> object | None:
    """Unwrap metadata and optionality without guessing among real unions."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (Union, UnionType):
        members = tuple(
            member for member in get_args(annotation) if member is not type(None)
        )
        if len(members) != 1:
            return None
        return _single_schema_annotation(members[0])
    return annotation


def _schema_field_path(
    schema: type[BaseModel], location: Sequence[object]
) -> str:
    """Retain only field names proven by the requested schema."""
    annotation: object | None = schema
    retained: list[str] = []
    for segment in location:
        annotation = _single_schema_annotation(annotation)
        if annotation is None:
            break
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if not isinstance(segment, str):
                break
            matched = next(
                (
                    (field_name, field)
                    for field_name, field in annotation.model_fields.items()
                    if segment == field_name
                    or (isinstance(field.alias, str) and segment == field.alias)
                    or (
                        isinstance(field.validation_alias, str)
                        and segment == field.validation_alias
                    )
                ),
                None,
            )
            if matched is None:
                break
            field_name, field = matched
            retained.append(field_name)
            annotation = field.annotation
            continue

        origin = get_origin(annotation)
        arguments = get_args(annotation)
        if origin in (dict, Mapping):
            annotation = arguments[1] if len(arguments) == 2 else None
            continue
        if origin in (list, set, frozenset, Sequence):
            annotation = arguments[0] if arguments else None
            continue
        if origin is tuple:
            if len(arguments) == 2 and arguments[1] is Ellipsis:
                annotation = arguments[0]
            elif isinstance(segment, int) and 0 <= segment < len(arguments):
                annotation = arguments[segment]
            else:
                annotation = None
            continue
        break
    return ".".join(retained) or "$"


def _validation_diagnostic(
    error: BaseException, *, attempt: int, schema: type[BaseModel]
) -> StructuredValidationDiagnostic:
    """Extract only bounded schema locations from a validation failure."""
    paths: list[str] = []
    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            items = errors(include_input=False)
        except TypeError:
            items = errors()
        for item in items:
            location = item.get("loc", ()) if isinstance(item, dict) else ()
            path = _schema_field_path(schema, location)
            paths.append(path)
            if len(paths) == _MAX_VALIDATION_FIELD_PATHS:
                break
    return StructuredValidationDiagnostic(
        attempt=attempt,
        field_paths=tuple(paths) or ("$",),
        category=validation_category(error),
    )


def _validation_summary(
    diagnostic: StructuredValidationDiagnostic,
    *,
    limit: int = _MAX_VALIDATION_SUMMARY_LENGTH,
) -> str:
    """Render only stable category and complete normalized field paths."""
    category = diagnostic.category or "schema_output"
    summary = f"category={category}; field_paths="
    if len(summary) >= limit:
        return summary[:limit]
    retained: list[str] = []
    for path in diagnostic.field_paths:
        separator = ", " if retained else ""
        if len(summary) + len(separator) + len(path) > limit:
            break
        retained.append(path)
        summary += f"{separator}{path}"
    return summary


def _validation_repair_guidance(
    diagnostic: StructuredValidationDiagnostic,
) -> str:
    """Return static repair guidance for categories with known remedies."""
    if diagnostic.category == "extra_forbidden":
        return (
            "Repair guidance: Return only properties declared by the schema. "
            "Remove undeclared properties and re-check every retained value.\n"
        )
    if diagnostic.category == "string_bounds":
        return (
            "Repair guidance: Satisfy every string constraint declared by the "
            "schema, including minLength, maxLength, and pattern.\n"
        )
    return ""


def _usage_from_response(response: Any) -> TokenUsage:
    """Map a Chat Completions usage object to project-owned token counts.

    Absent usage maps to zero tokens. When usage exists, both prompt and
    completion token counts must be non-negative integers and any supplied
    total must agree with their sum; anything else is malformed.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    input_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or input_tokens < 0
        or isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        raise ProviderResponseError(
     "DeepSeek response contained malformed usage",
     failure_origin="local_response",
 )
    total_tokens = getattr(usage, "total_tokens", None)
    if total_tokens is not None and (
        isinstance(total_tokens, bool)
        or not isinstance(total_tokens, int)
        or total_tokens != input_tokens + output_tokens
    ):
        raise ProviderResponseError(
     "DeepSeek response contained malformed usage",
     failure_origin="local_response",
 )
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _responses_usage_from_response(response: Any) -> TokenUsage:
    """Map a Responses usage object to project-owned token counts."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if (
        isinstance(input_tokens, bool)
        or not isinstance(input_tokens, int)
        or input_tokens < 0
        or isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        response = None
        usage = None
        input_tokens = None
        output_tokens = None
        raise ProviderResponseError(
     "DeepSeek response contained malformed usage",
     failure_origin="local_response",
 )
    total_tokens = getattr(usage, "total_tokens", None)
    if total_tokens is not None and (
        isinstance(total_tokens, bool)
        or not isinstance(total_tokens, int)
        or total_tokens != input_tokens + output_tokens
    ):
        response = None
        usage = None
        input_tokens = None
        output_tokens = None
        total_tokens = None
        raise ProviderResponseError(
     "DeepSeek response contained malformed usage",
     failure_origin="local_response",
 )
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _responses_finish_reason(response: Any) -> FinishReasonCategory:
    """Map the Responses terminal status to the finite project taxonomy."""
    status = getattr(response, "status", None)
    if status == "completed":
        return "stop"
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        reason = getattr(details, "reason", None)
        if reason == "max_output_tokens":
            return "length"
        if reason == "content_filter":
            return "content_filter"
    return "other"


def _normalize_finish_reason(value: object) -> FinishReasonCategory:
    """Map an untrusted provider finish value to the finite safe taxonomy."""
    if not isinstance(value, str) or len(value) > 64:
        return "other"
    if any(unicodedata.category(character).startswith("C") for character in value):
        return "other"
    normalized = value.strip().lower()
    if normalized in _FINISH_REASON_CATEGORIES:
        return cast(FinishReasonCategory, normalized)
    return "other"


def _response_finish_reason(response: Any) -> object:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        return None
    return getattr(choices[0], "finish_reason", None)


def _response_telemetry(
    response: Any,
    *,
    configured_max_tokens: int,
    request_attempt: int,
    structured_attempt: int | None = None,
) -> ProviderResponseTelemetry:
    return ProviderResponseTelemetry(
        finish_reason_category=_normalize_finish_reason(
            _response_finish_reason(response)
        ),
        configured_max_tokens=configured_max_tokens,
        usage=_usage_from_response(response),
        request_attempt=request_attempt,
        structured_attempt=structured_attempt,
    )


def _choice_text(
    response: Any,
    *,
    allow_empty: bool = False,
    finish_reason_category: FinishReasonCategory | None = None,
) -> str:
    """Extract and trim text from exactly one clean Chat Completions choice.

    Fail-closed on malformed shapes and any non-``stop`` finish reason, so a
    truncated, filtered, or resource-failed response never surfaces as
    output. ``allow_empty`` permits a blank string for the structured
    validation path; plain completion keeps blank content operational.
    """
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        raise ProviderResponseError(
     "DeepSeek response contained malformed choices",
     failure_origin="local_response",
 )
    choice = choices[0]
    is_stop = (
        finish_reason_category == "stop"
        if finish_reason_category is not None
        else getattr(choice, "finish_reason", None) == "stop"
    )
    if not is_stop:
        raise ProviderResponseError(
     "DeepSeek response did not stop cleanly",
     failure_origin="local_response",
 )
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not isinstance(content, str):
        raise ProviderResponseError(
     "DeepSeek response contained malformed content",
     failure_origin="local_response",
 )
    text = content.strip()
    if not text and not allow_empty:
        raise ProviderResponseError(
     "DeepSeek response contained malformed content",
     failure_origin="local_response",
 )
    return text


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


def _native_outcome(
    response: Any,
    *,
    allowed: set[str],
    finish_reason_category: FinishReasonCategory,
) -> tuple[NativeToolCall | None, str | None, str | None]:
    """Read exactly one native tool call or one non-blank final answer.

    Returns ``(tool_call, final_answer, rejection_reason)`` with exactly one of
    the three set. Only the typed ``message.tool_calls`` field can select a
    tool: text is never inspected for tool markup, so DSML, XML, Markdown
    fences, and JSON action envelopes in the message body cannot request
    execution.

    This function never raises. A rejection is returned instead, so the caller
    can clear its own provider-adjacent locals before that rejection becomes a
    public error whose traceback would otherwise retain the response text.

    Arguments must be a non-blank string. That is stricter than "a string":
    every tool in the registry requires at least one argument, so a blank
    argument payload can never be a legitimate call.
    """
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        return None, None, "DeepSeek response contained malformed choices"
    message = getattr(choices[0], "message", None)
    if message is None:
        return None, None, "DeepSeek response contained no message"
    raw_calls = getattr(message, "tool_calls", None)
    calls = raw_calls if isinstance(raw_calls, (list, tuple)) else ()
    # A container the provider did send, but not as a sequence, is a malformed
    # envelope: it must be rejected rather than read as "no call at all".
    malformed_calls = raw_calls is not None and not isinstance(
        raw_calls, (list, tuple)
    )
    if malformed_calls:
        return None, None, (
            "DeepSeek native tool response carried a malformed tool_calls field"
        )

    if finish_reason_category == "tool_calls":
        if len(calls) != 1:
            return None, None, (
                "DeepSeek native tool response must carry exactly one tool call"
            )
        call = calls[0]
        if getattr(call, "type", None) != "function":
            return None, None, (
                "DeepSeek native tool response carried a non-function call"
            )
        function = getattr(call, "function", None)
        name = getattr(function, "name", None) if function is not None else None
        if not isinstance(name, str) or name not in allowed:
            return None, None, (
                "DeepSeek native tool response named an unavailable tool"
            )
        arguments = getattr(function, "arguments", None)
        if not isinstance(arguments, str) or not arguments.strip():
            return None, None, (
                "DeepSeek native tool response carried malformed arguments"
            )
        return NativeToolCall(tool_name=name, arguments_json=arguments), None, None

    if finish_reason_category != "stop":
        return None, None, "DeepSeek response did not stop cleanly"
    if calls:
        return None, None, (
            "DeepSeek native tool response mixed a final answer with a tool call"
        )
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        return None, None, (
            "DeepSeek native tool response carried no usable final answer"
        )
    return None, content.strip(), None


def _translate_deepseek_error(error: Exception) -> ProviderError:
    """Translate SDK operational failures to safe typed provider errors.

    Returns a fresh project error instead of raising it. Raising from inside
    this frame would put the frame -- and therefore the SDK exception it holds
    as a parameter local -- on the new error's traceback, where a caller could
    still reach the SDK object through ``tb_frame.f_locals`` even after
    ``with_retries`` has cleared ``__cause__`` and ``__context__``. Returning
    keeps the SDK object confined to the caller's handler, whose locals the
    interpreter clears when the handler exits.
    """
    sdk = _openai_errors()
    if isinstance(error, sdk.APITimeoutError):
        return ProviderTimeoutError("DeepSeek request timed out")
    if isinstance(error, sdk.RateLimitError):
        return ProviderRateLimitError("DeepSeek rate limit exceeded")
    if isinstance(error, sdk.APIConnectionError):
        return ProviderResponseError(
            "DeepSeek connection failed",
            failure_origin="sdk",
            retryable=True,
            failure_category="transport",
        )
    if isinstance(error, sdk.APIStatusError):
        status = error.status_code
        return ProviderResponseError(
            f"DeepSeek request failed with status {status}",
            failure_origin="sdk",
            retryable=status >= 500 or status in (408, 409),
            failure_category="http",
            http_status_code=status,
        )
    # Unreachable while every call site catches exactly the four SDK types
    # handled above. Fail loudly rather than invent a category for a type
    # whose public semantics nobody has decided.
    raise AssertionError("untranslated DeepSeek SDK error type")


def _set_span_result(span: Any, telemetry: ProviderResponseTelemetry) -> None:
    span.set_outputs(telemetry.model_dump(mode="json"))
    span.set_token_usage(
        input_tokens=telemetry.usage.input_tokens,
        output_tokens=telemetry.usage.output_tokens,
        total_tokens=telemetry.usage.total_tokens,
    )


def _responses_request_options(
    config: LLMConfig,
    agent_name: str | None,
) -> tuple[EffectiveModelConfig, dict[str, object], dict[str, JsonValue]]:
    effective = config.resolve_for(agent_name)
    resolved = resolve_request_settings("deepseek", effective)
    request: dict[str, object] = {
        "model": effective.model,
        "reasoning": {
            "effort": (
                resolved.reasoning_effort
                if resolved.reasoning_effort is not None
                else "none"
            )
        },
    }
    if resolved.include_temperature:
        request["temperature"] = config.temperature
    metadata: dict[str, JsonValue] = {
        "provider": "deepseek",
        "thinking_mode": effective.thinking_mode,
        "requested_reasoning_effort": effective.reasoning_effort,
    }
    if agent_name is not None:
        metadata["agent_name"] = agent_name
    if resolved.reasoning_effort is not None:
        metadata["effective_reasoning_effort"] = resolved.reasoning_effort
    return effective, request, metadata


class DeepSeekChatProvider:
    """Async plain and structured-output access through DeepSeek Chat Completions."""

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

        ``deepseek-v4-flash`` is requested as a bare alias; the API may
        answer as a dated snapshot such as ``DeepSeek-V4-Flash-0731``. The
        evaluation harness records the requested alias *and* what was
        actually served, and never substitutes one for the other.
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
        resolved = resolve_request_settings("deepseek", effective)
        request: dict[str, object] = {
            "model": effective.model,
            "max_tokens": self._config.max_tokens,
            "extra_body": {"thinking": {"type": effective.thinking_mode}},
        }
        if resolved.reasoning_effort is not None:
            request["reasoning_effort"] = resolved.reasoning_effort
        if resolved.include_temperature:
            request["temperature"] = self._config.temperature
        metadata: dict[str, JsonValue] = {
            "provider": "deepseek",
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
        payload = _translated_messages(messages)
        request_attempt = 0
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
                    nonlocal request_attempt
                    request_attempt += 1
                    try:
                        return await self._client.chat.completions.create(
                            **{**request, "messages": payload}
                        )
                    except (
                        _sdk.APITimeoutError,
                        _sdk.RateLimitError,
                        _sdk.APIConnectionError,
                        _sdk.APIStatusError,
                    ) as error:
                        raise _translate_deepseek_error(error)
                    except _sdk.OpenAIError as error:
                        raise ProviderResponseError(
                            "DeepSeek chat request failed",
                            failure_origin="sdk",
                        ) from error

                response = await with_retries(
                    _request,
                    retry_count=self._config.retry_count,
                    initial_delay=self._config.retry_initial_delay,
                    max_delay=self._config.retry_max_delay,
                )
                telemetry = _response_telemetry(
                    response,
                    configured_max_tokens=self._config.max_tokens,
                    request_attempt=request_attempt,
                )
                _set_span_result(span, telemetry)
                if telemetry.finish_reason_category == "length":
                    raise ProviderOutputLimitError(telemetry)
                text = _choice_text(
                    response,
                    finish_reason_category=telemetry.finish_reason_category,
                )
                self._last_model_returned = (
                    getattr(response, "model", None) or effective.model
                )
                return ChatResult(
                    text=text, model=effective.model, usage=telemetry.usage
                )
        except ProviderError:
            # Documentary guard: project-owned errors are already typed,
            # safe, and content-free, so they pass through untouched. The
            # span context manager above finalizes telemetry on every path;
            # this clause only makes the typed-error contract explicit.
            raise

    async def _structured_attempt(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
        *,
        model: str,
        request: dict[str, object],
        metadata: dict[str, JsonValue],
        configured_max_tokens: int,
        attempt: int,
    ) -> SchemaT:
        async with self._tracker.llm_span(
            model,
            {
                **metadata,
                "operation": "structured_output",
                "attempt": attempt,
                "message_count": len(messages),
            },
        ) as span:
            _sdk = _openai_errors()
            request_attempt = 0

            async def _request() -> Any:
                nonlocal request_attempt
                request_attempt += 1
                try:
                    return await self._client.chat.completions.create(
                        **{
                            **request,
                            "messages": messages,
                            "response_format": {"type": "json_object"},
                        }
                    )
                except (
                    _sdk.APITimeoutError,
                    _sdk.RateLimitError,
                    _sdk.APIConnectionError,
                    _sdk.APIStatusError,
                ) as error:
                    raise _translate_deepseek_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "DeepSeek structured output request failed",
                        failure_origin="sdk",
                    ) from error

            response = await with_retries(
                _request,
                retry_count=self._config.retry_count,
                initial_delay=self._config.retry_initial_delay,
                max_delay=self._config.retry_max_delay,
            )
            telemetry = _response_telemetry(
                response,
                configured_max_tokens=configured_max_tokens,
                request_attempt=request_attempt,
                structured_attempt=attempt,
            )
            _set_span_result(span, telemetry)
            if telemetry.finish_reason_category == "length":
                raise ProviderOutputLimitError(telemetry)
            text = _choice_text(
                response,
                allow_empty=True,
                finish_reason_category=telemetry.finish_reason_category,
            )
            try:
                parsed = schema.model_validate_json(text)
            except (json.JSONDecodeError, ValidationError) as error:
                diagnostic = _validation_diagnostic(
                    error,
                    attempt=attempt,
                    schema=schema,
                )
            else:
                self._last_model_returned = (
                    getattr(response, "model", None) or model
                )
                return parsed
            # Raise after the validation handler exits so the provider-bearing
            # ValidationError is not retained through ``__context__``, while
            # still marking this traced attempt as failed.
            raise _StructuredValidationFailure(schema.__name__, diagnostic) from None

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
        request = {**request, "max_tokens": resolved_max_tokens}
        instruction = _json_instruction(schema)
        current_messages = [
            *_translated_messages(messages),
            {"role": "system", "content": instruction.content},
        ]

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
                    configured_max_tokens=resolved_max_tokens,
                    attempt=attempt,
                )
            except _StructuredValidationFailure as error:
                diagnostics.append(error.diagnostic)
                if attempt == 2:
                    final_error = StructuredOutputError(
                        f"DeepSeek output failed {schema.__name__} validation "
                        "after one repair attempt",
                        diagnostics=tuple(diagnostics),
                    )
                    break
                schema_json = json.dumps(
                    schema.model_json_schema(), sort_keys=True, separators=(",", ":")
                )
                repair_guidance = _validation_repair_guidance(error.diagnostic)
                repair = (
                    f"The previous JSON response failed {schema.__name__} "
                    "validation. Return only one JSON object that validates "
                    "against the supplied JSON Schema. Do not add Markdown or "
                    "explanatory text. "
                    f"Validation summary: {_validation_summary(error.diagnostic)}\n"
                    f"{repair_guidance}"
                    f"JSON Schema:\n{schema_json}"
                )
                current_messages = [
                    *current_messages,
                    {"role": "system", "content": repair},
                ]

        if final_error is None:
            raise AssertionError("structured output attempt loop did not return")

        # Do not raise while handling the internal validation failure: that
        # would retain it through ``__context__``/``__cause__``. Clear all
        # provider-adjacent locals before the public error's traceback is
        # captured, leaving only the bounded typed diagnostics.
        self = None
        messages = []
        current_messages = []
        request = {}
        metadata = {}
        effective = None
        agent_name = None
        schema = BaseModel
        instruction = None
        schema_json = ""
        repair = ""
        repair_guidance = ""
        raise final_error

    async def complete_react(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition],
        *,
        agent_name: str | None = None,
        max_tokens: int | None = None,
    ) -> NativeToolTurn:
        """One native ReAct turn: a provider tool call or a final answer.

        The request carries real function definitions with
        ``tool_choice="auto"``. Function-specific and ``required`` tool choice
        are never sent: with thinking enabled DeepSeek answers both with HTTP
        400, so the model decides for itself whether to call a tool.

        There is no ``response_format`` and no structured repair. The native
        tool-call envelope is the contract, and a malformed one fails closed
        rather than being retried into a possibly different shape.
        """
        if not messages:
            raise ValueError("messages must contain at least one item")
        if not tools:
            raise ValueError("tools must contain at least one item")
        resolved_max_tokens = _resolve_max_tokens(
            self._config.max_tokens, max_tokens
        )
        effective, request, metadata = self._request_options(agent_name)
        request = {**request, "max_tokens": resolved_max_tokens}
        payload = _translated_messages(messages)
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
                    return await self._client.chat.completions.create(
                        **{
                            **request,
                            "messages": payload,
                            "tools": [
                                {
                                    "type": "function",
                                    "function": definition.model_dump(mode="json"),
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
                    raise _translate_deepseek_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "DeepSeek native tool request failed",
                        failure_origin="sdk",
                    ) from error

            response = await with_retries(
                _request,
                retry_count=self._config.retry_count,
                initial_delay=self._config.retry_initial_delay,
                max_delay=self._config.retry_max_delay,
            )
            telemetry: ProviderResponseTelemetry | None = None
            tool_call: NativeToolCall | None = None
            final_answer: str | None = None
            rejection: str | None = None
            failure: ProviderError | None = None
            try:
                telemetry = _response_telemetry(
                    response,
                    configured_max_tokens=resolved_max_tokens,
                    request_attempt=request_attempt,
                )
            except ProviderResponseError as error:
                # A malformed usage shape is rejected *before* the clearing
                # block below, so the rejection has to be replaced with a
                # traceback-free copy: re-raising the caught object would keep
                # the frames that still hold the raw response.
                failure = _fresh_provider_error(error)
            if failure is None:
                _set_span_result(span, telemetry)
                if telemetry.finish_reason_category == "length":
                    failure = ProviderOutputLimitError(telemetry)
                else:
                    tool_call, final_answer, rejection = _native_outcome(
                        response,
                        allowed=allowed,
                        finish_reason_category=telemetry.finish_reason_category,
                    )
                    if rejection is not None:
                        failure = ProviderResponseError(
            rejection, failure_origin="local_response"
        )
            if failure is None:
                self._last_model_returned = (
                    getattr(response, "model", None) or effective.model
                )
                return NativeToolTurn(
                    model=effective.model,
                    usage=telemetry.usage,
                    tool_call=tool_call,
                    final_answer=final_answer,
                )

            # Do not raise while holding provider-adjacent locals: the public
            # error's traceback would otherwise retain the response text, the
            # prompt, and the tool arguments. ``complete_structured`` clears its
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
            telemetry = None
            tool_call = None
            final_answer = None
            rejection = None
            raise failure


class _DeepSeekSchemaStructuredProvider(DeepSeekChatProvider):
    """DeepSeek structured output through native Responses ``json_schema``.

    Chat Completions JSON mode only guarantees JSON *syntax*; it neither
    accepts a schema nor enforces one, so conformance is left to the model and
    enforced locally by Pydantic. That was sufficient for the judge only after
    this transport existed, and it is not sufficient for the target agents: a
    live Critic canary recorded ``json_invalid`` at ``$`` on both the initial
    attempt and the one repair, which is reachable only when the provider
    returns non-empty text that is not parseable JSON at all.

    This base asks the model for the exact requested schema, so the provider
    constrains its own decoding. Pydantic remains the local authority, the
    one-repair flow is unchanged, and every failure stays in the existing
    typed taxonomy. Plain completions keep the Chat Completions path; only
    ``complete_structured`` moves.
    """

    async def _responses_structured_attempt(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
        *,
        model: str,
        request: dict[str, object],
        metadata: dict[str, JsonValue],
        configured_max_tokens: int,
        attempt: int,
    ) -> SchemaT:
        async with self._tracker.llm_span(
            model,
            {
                **metadata,
                "operation": "structured_output",
                "attempt": attempt,
                "message_count": len(messages),
            },
        ) as span:
            _sdk = _openai_errors()
            request_attempt = 0

            async def _request() -> Any:
                nonlocal request_attempt
                request_attempt += 1
                try:
                    return await self._client.responses.create(
                        **{
                            **request,
                            "input": messages,
                            "max_output_tokens": configured_max_tokens,
                            "text": {
                                "format": {
                                    "type": "json_schema",
                                    "name": schema.__name__,
                                    "schema": schema.model_json_schema(),
                                }
                            },
                        }
                    )
                except (
                    _sdk.APITimeoutError,
                    _sdk.RateLimitError,
                    _sdk.APIConnectionError,
                    _sdk.APIStatusError,
                ) as error:
                    raise _translate_deepseek_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "DeepSeek Responses request failed",
                        failure_origin="sdk",
                    ) from error

            response = await with_retries(
                _request,
                retry_count=self._config.retry_count,
                initial_delay=self._config.retry_initial_delay,
                max_delay=self._config.retry_max_delay,
            )
            telemetry = ProviderResponseTelemetry(
                finish_reason_category=_responses_finish_reason(response),
                configured_max_tokens=configured_max_tokens,
                usage=_responses_usage_from_response(response),
                request_attempt=request_attempt,
                structured_attempt=attempt,
            )
            _set_span_result(span, telemetry)
            if telemetry.finish_reason_category == "length":
                response = None
                raise ProviderOutputLimitError(telemetry)
            if telemetry.finish_reason_category != "stop":
                response = None
                raise ProviderResponseError(
                    "DeepSeek Responses request did not complete cleanly",
                    failure_origin="local_response",
                )
            text = getattr(response, "output_text", None)
            if not isinstance(text, str):
                response = None
                raise ProviderResponseError(
                    "DeepSeek Responses output did not contain text",
                    failure_origin="local_response",
                )
            if not text.strip():
                # DeepSeek documents that JSON Output "may occasionally return
                # empty content". An empty body must not be reported as
                # malformed JSON: both would otherwise record json_invalid at
                # the root, making the two failure modes indistinguishable in
                # an artifact. This mirrors the non-empty requirement
                # ``_choice_text`` already applies on the chat path.
                response = None
                raise _StructuredValidationFailure(
                    schema.__name__,
                    StructuredValidationDiagnostic(
                        attempt=attempt,
                        field_paths=("$",),
                        category="schema_output",
                    ),
                ) from None
            try:
                parsed = schema.model_validate_json(text)
            except (json.JSONDecodeError, ValidationError) as error:
                diagnostic = _validation_diagnostic(
                    error,
                    attempt=attempt,
                    schema=schema,
                )
            else:
                self._last_model_returned = (
                    getattr(response, "model", None) or model
                )
                return parsed
            response = None
            text = ""
            parsed = None
            raise _StructuredValidationFailure(schema.__name__, diagnostic) from None

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
        effective, request, metadata = _responses_request_options(
            self._config, agent_name
        )
        instruction = _json_instruction(schema)
        current_messages = [
            *_translated_messages(messages),
            {"role": "system", "content": instruction.content},
        ]

        diagnostics: list[StructuredValidationDiagnostic] = []
        final_error: StructuredOutputError | None = None
        for attempt in (1, 2):
            try:
                return await self._responses_structured_attempt(
                    current_messages,
                    schema,
                    model=effective.model,
                    request=request,
                    metadata=metadata,
                    configured_max_tokens=resolved_max_tokens,
                    attempt=attempt,
                )
            except _StructuredValidationFailure as error:
                diagnostics.append(error.diagnostic)
                if attempt == 2:
                    final_error = StructuredOutputError(
                        f"DeepSeek output failed {schema.__name__} validation "
                        "after one repair attempt",
                        diagnostics=tuple(diagnostics),
                    )
                    break
                schema_json = json.dumps(
                    schema.model_json_schema(), sort_keys=True, separators=(",", ":")
                )
                repair_guidance = _validation_repair_guidance(error.diagnostic)
                repair = (
                    f"The previous JSON response failed {schema.__name__} "
                    "validation. Return only one JSON object that validates "
                    "against the supplied JSON Schema. Do not add Markdown or "
                    "explanatory text. "
                    f"Validation summary: {_validation_summary(error.diagnostic)}\n"
                    f"{repair_guidance}"
                    f"JSON Schema:\n{schema_json}"
                )
                current_messages = [
                    *current_messages,
                    {"role": "system", "content": repair},
                ]

        if final_error is None:
            raise AssertionError("structured output attempt loop did not return")

        # Do not raise while handling the internal validation failure: that
        # would retain it through ``__context__``/``__cause__``. Clear all
        # provider-adjacent locals before the public error's traceback is
        # captured, leaving only the bounded typed diagnostics.
        self = None
        messages = []
        current_messages = []
        request = {}
        metadata = {}
        effective = None
        agent_name = None
        schema = BaseModel
        instruction = None
        schema_json = ""
        repair = ""
        repair_guidance = ""
        raise final_error


class DeepSeekJudgeProvider(_DeepSeekSchemaStructuredProvider):
    """DeepSeek judge access through the native Responses schema transport."""


class DeepSeekSchemaChatProvider(_DeepSeekSchemaStructuredProvider):
    """DeepSeek target-agent access with schema-enforced structured output.

    Distinct from :class:`DeepSeekJudgeProvider` so the judge and target
    transports stay separately selectable and separately testable: the
    evaluation factory builds the target through ``build_chat_provider`` and
    the judge through ``build_judge_provider``, and a single class would make
    those two decisions impossible to vary independently.
    """