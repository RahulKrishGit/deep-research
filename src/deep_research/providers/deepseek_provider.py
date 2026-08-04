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
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, TypeVar

from pydantic import BaseModel, JsonValue, ValidationError

from deep_research.observability import TokenUsage, Tracker
from deep_research.providers.capabilities import resolve_request_settings
from deep_research.providers.contracts import (
    ChatMessage,
    ChatResult,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.utils.config import EffectiveModelConfig, LLMConfig

SchemaT = TypeVar("SchemaT", bound=BaseModel)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

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
        max_retries=config.retry_count,
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

    The message names the schema but never the provider output, and the
    summary is sanitized to pydantic error locations and messages, which do
    not embed the invalid text.
    """

    def __init__(self, schema_name: str, summary: str) -> None:
        super().__init__(f"DeepSeek output failed {schema_name} validation")
        self.summary = summary


def _validation_summary(error: BaseException, *, limit: int = 1000) -> str:
    """Format validation diagnostics without any provider output.

    ``str(ValidationError)`` embeds input values, so the summary is built
    from the structured error items instead and capped at ``limit``
    characters for a bounded repair prompt.
    """
    errors = getattr(error, "errors", None)
    if callable(errors):
        lines: list[str] = []
        for item in errors():
            location = ".".join(str(part) for part in item.get("loc", ()))
            message = item.get("msg", "")
            lines.append(f"{location}: {message}" if location else message)
        summary = "; ".join(lines)
    else:
        summary = str(error)
    return summary[:limit]


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


def _choice_text(response: Any, *, allow_empty: bool = False) -> str:
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
    if getattr(choice, "finish_reason", None) != "stop":
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
        raise ProviderResponseError("DeepSeek connection failed") from error
    if isinstance(error, sdk.APIStatusError):
        raise ProviderResponseError(
            f"DeepSeek request failed with status {error.status_code}"
        ) from error
    raise error


def _set_span_result(span: Any, response: Any, usage: TokenUsage) -> None:
    span.set_outputs(
        {
            "provider": "deepseek",
            "response_id": getattr(response, "id", "unknown"),
        }
    )
    span.set_token_usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
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
                try:
                    response = await self._client.chat.completions.create(
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
                text = _choice_text(response)
                usage = _usage_from_response(response)
                _set_span_result(span, response, usage)
                return ChatResult(text=text, model=effective.model, usage=usage)
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
            try:
                response = await self._client.chat.completions.create(
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
            text = _choice_text(response, allow_empty=True)
            usage = _usage_from_response(response)
            _set_span_result(span, response, usage)
            try:
                return schema.model_validate_json(text)
            except (json.JSONDecodeError, ValidationError) as error:
                raise _StructuredValidationFailure(
                    schema.__name__, _validation_summary(error)
                ) from error

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
                if attempt == 2:
                    raise StructuredOutputError(
                        f"DeepSeek output failed {schema.__name__} validation "
                        "after one repair attempt"
                    ) from error
                schema_json = json.dumps(
                    schema.model_json_schema(), sort_keys=True, separators=(",", ":")
                )
                repair = (
                    f"The previous JSON response failed {schema.__name__} "
                    "validation. Return only one JSON object that validates "
                    "against the supplied JSON Schema. Do not add Markdown or "
                    "explanatory text. "
                    f"Validation summary: {error.summary}\n"
                    f"JSON Schema:\n{schema_json}"
                )
                current_messages = [
                    *current_messages,
                    {"role": "system", "content": repair},
                ]

        raise AssertionError("structured output attempt loop did not return")
