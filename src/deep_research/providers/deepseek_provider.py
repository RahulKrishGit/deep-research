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
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, TypeVar, cast

from pydantic import BaseModel, JsonValue, ValidationError

from deep_research.observability import TokenUsage, Tracker
from deep_research.providers.capabilities import resolve_request_settings
from deep_research.providers.contracts import (
    ChatMessage,
    ChatResult,
    FinishReasonCategory,
    ProviderConfigurationError,
    ProviderError,
    ProviderOutputLimitError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderResponseTelemetry,
    ProviderTimeoutError,
    StructuredOutputError,
    StructuredValidationDiagnostic,
)
from deep_research.providers.retry import with_retries
from deep_research.utils.config import EffectiveModelConfig, LLMConfig

SchemaT = TypeVar("SchemaT", bound=BaseModel)
_MAX_VALIDATION_FIELD_PATHS = 16
_MAX_VALIDATION_SUMMARY_LENGTH = 1000

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


def _validation_diagnostic(
    error: BaseException, *, attempt: int
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
            path = ".".join(str(part) for part in location) or "$"
            paths.append(path)
            if len(paths) == _MAX_VALIDATION_FIELD_PATHS:
                break
    return StructuredValidationDiagnostic(
        attempt=attempt,
        field_paths=tuple(paths) or ("$",),
        category="schema_output",
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
        raise ProviderResponseError("DeepSeek response contained malformed usage")
    total_tokens = getattr(usage, "total_tokens", None)
    if total_tokens is not None and (
        isinstance(total_tokens, bool)
        or not isinstance(total_tokens, int)
        or total_tokens != input_tokens + output_tokens
    ):
        raise ProviderResponseError("DeepSeek response contained malformed usage")
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


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
        raise ProviderResponseError("DeepSeek response contained malformed choices")
    choice = choices[0]
    is_stop = (
        finish_reason_category == "stop"
        if finish_reason_category is not None
        else getattr(choice, "finish_reason", None) == "stop"
    )
    if not is_stop:
        raise ProviderResponseError("DeepSeek response did not stop cleanly")
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not isinstance(content, str):
        raise ProviderResponseError("DeepSeek response contained malformed content")
    text = content.strip()
    if not text and not allow_empty:
        raise ProviderResponseError("DeepSeek response contained malformed content")
    return text


def _raise_deepseek_error(error: Exception) -> None:
    """Translate SDK operational failures to safe typed provider errors.

    Any exception outside the handled SDK types falls through and is
    re-raised unchanged; callers decide how to classify it. In
    ``complete`` only the handled types can reach this helper, so the
    fallthrough is defensive rather than reachable there.
    """
    sdk = _openai_errors()
    if isinstance(error, sdk.APITimeoutError):
        raise ProviderTimeoutError("DeepSeek request timed out") from error
    if isinstance(error, sdk.RateLimitError):
        raise ProviderRateLimitError("DeepSeek rate limit exceeded") from error
    if isinstance(error, sdk.APIConnectionError):
        raise ProviderResponseError(
            "DeepSeek connection failed",
            retryable=True,
            failure_category="transport",
        ) from error
    if isinstance(error, sdk.APIStatusError):
        status = error.status_code
        raise ProviderResponseError(
            f"DeepSeek request failed with status {status}",
            retryable=status >= 500 or status in (408, 409),
            failure_category="http",
            http_status_code=status,
        ) from error
    raise error


def _set_span_result(span: Any, telemetry: ProviderResponseTelemetry) -> None:
    span.set_outputs(telemetry.model_dump(mode="json"))
    span.set_token_usage(
        input_tokens=telemetry.usage.input_tokens,
        output_tokens=telemetry.usage.output_tokens,
        total_tokens=telemetry.usage.total_tokens,
    )


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
                        _raise_deepseek_error(error)
                    except _sdk.OpenAIError as error:
                        raise ProviderResponseError(
                            "DeepSeek chat request failed"
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
                    _raise_deepseek_error(error)
                except _sdk.OpenAIError as error:
                    raise ProviderResponseError(
                        "DeepSeek structured output request failed"
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
                diagnostic = _validation_diagnostic(error, attempt=attempt)
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
    ) -> SchemaT:
        if not messages:
            raise ValueError("messages must contain at least one item")
        effective, request, metadata = self._request_options(agent_name)
        instruction = _json_instruction(schema)
        current_messages = [
            *_translated_messages(messages),
            {"role": "system", "content": instruction.content},
        ]

        diagnostics: list[StructuredValidationDiagnostic] = []
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
                    raise StructuredOutputError(
                        f"DeepSeek output failed {schema.__name__} validation "
                        "after one repair attempt",
                        diagnostics=tuple(diagnostics),
                    ) from error
                schema_json = json.dumps(
                    schema.model_json_schema(), sort_keys=True, separators=(",", ":")
                )
                repair = (
                    f"The previous JSON response failed {schema.__name__} "
                    "validation. Return only one JSON object that validates "
                    "against the supplied JSON Schema. Do not add Markdown or "
                    "explanatory text. "
                    f"Validation summary: {_validation_summary(error.diagnostic)}\n"
                    f"JSON Schema:\n{schema_json}"
                )
                current_messages = [
                    *current_messages,
                    {"role": "system", "content": repair},
                ]

        raise AssertionError("structured output attempt loop did not return")
