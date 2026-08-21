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
    OpenAIProviderError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.utils.config import EffectiveModelConfig, LLMConfig

SchemaT = TypeVar("SchemaT", bound=BaseModel)

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
        max_retries=config.retry_count,
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
        raise ProviderResponseError("OpenAI response contained malformed usage")
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
        raise ProviderResponseError("OpenAI connection failed") from error
    if isinstance(error, sdk.APIStatusError):
        raise ProviderResponseError(
            f"OpenAI request failed with status {error.status_code}"
        ) from error
    raise error


class _StructuredValidationFailure(RuntimeError):
    def __init__(self, schema_name: str, output_text: str) -> None:
        super().__init__(f"OpenAI output failed {schema_name} validation")
        self.output_text = output_text


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
                try:
                    response = await self._client.responses.create(
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
                    raise ProviderResponseError("OpenAI chat request failed") from error
                output_text = getattr(response, "output_text", None)
                if not isinstance(output_text, str):
                    raise ProviderResponseError(
                        "OpenAI response did not contain text output"
                    )
                text = output_text.strip()
                if not text:
                    raise ProviderResponseError(
                        "OpenAI response did not contain text output"
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
            try:
                response = await self._client.responses.parse(
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
                    "OpenAI structured output request failed"
                ) from error
            except ValidationError as error:
                raise _StructuredValidationFailure(
                    schema.__name__, str(error)
                ) from error
            usage = _usage_from_response(response)
            _set_span_result(span, response, usage)
            parsed = getattr(response, "output_parsed", None)
            if not isinstance(parsed, schema):
                raise _StructuredValidationFailure(
                    schema.__name__, str(getattr(response, "output_text", ""))
                )
            self._last_model_returned = getattr(response, "model", None) or model
            return parsed

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
        current_messages = list(messages)

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
                        f"OpenAI output failed {schema.__name__} validation "
                        "after one repair attempt"
                    ) from error
                repair_instruction = (
                    f"The previous response failed {schema.__name__} validation. "
                    "Return a corrected response that matches the supplied schema "
                    "exactly. Invalid response: "
                    f"{error.output_text}"
                )
                current_messages = [
                    *messages,
                    ChatMessage(role="developer", content=repair_instruction),
                ]

        raise AssertionError("structured output attempt loop did not return")
