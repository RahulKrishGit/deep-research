"""OpenAI chat and embedding providers with project-owned contracts."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, Literal, TypeVar

from openai import APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deep_research.observability import TokenUsage, Tracker
from deep_research.utils.config import LLMConfig

MessageRole = Literal["developer", "system", "user", "assistant"]
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatMessage(ProviderContract):
    role: MessageRole
    content: str = Field(min_length=1)


class ChatResult(ProviderContract):
    text: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


class OpenAIProviderError(RuntimeError):
    """Base caller-facing error for the OpenAI provider boundary."""


class ProviderConfigurationError(OpenAIProviderError):
    """Provider configuration is absent or invalid."""


class ProviderTimeoutError(OpenAIProviderError):
    """An OpenAI request exceeded its timeout."""


class ProviderRateLimitError(OpenAIProviderError):
    """OpenAI rejected a request due to rate limiting."""


class ProviderResponseError(OpenAIProviderError):
    """OpenAI returned an unusable response or status error."""


class StructuredOutputError(OpenAIProviderError):
    """Structured output remained invalid after one repair request."""


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
    return AsyncOpenAI(
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
        input_tokens = getattr(usage, "prompt_tokens", 0)
    output_tokens = getattr(usage, "output_tokens", 0)
    return TokenUsage(
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        total_tokens=(input_tokens or 0) + (output_tokens or 0),
    )


def _set_span_result(span: Any, response: Any, usage: TokenUsage) -> None:
    span.set_outputs(
        {
            "provider": "openai",
            "response_id": getattr(response, "id", "unknown"),
        }
    )
    span.set_token_usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


def _raise_provider_error(error: Exception) -> None:
    if isinstance(error, APITimeoutError):
        raise ProviderTimeoutError("OpenAI request timed out") from error
    if isinstance(error, RateLimitError):
        raise ProviderRateLimitError("OpenAI rate limit exceeded") from error
    if isinstance(error, APIStatusError):
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

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        agent_name: str | None = None,
    ) -> ChatResult:
        if not messages:
            raise ValueError("messages must contain at least one item")
        model = self._config.model_for(agent_name)
        payload = [message.model_dump(mode="json") for message in messages]
        try:
            async with self._tracker.llm_span(
                model,
                {
                    "provider": "openai",
                    "operation": "chat",
                    "message_count": len(payload),
                },
            ) as span:
                try:
                    response = await self._client.responses.create(
                        model=model,
                        input=payload,
                        temperature=self._config.temperature,
                        max_output_tokens=self._config.max_tokens,
                    )
                except (APITimeoutError, RateLimitError, APIStatusError) as error:
                    _raise_provider_error(error)
                text = str(getattr(response, "output_text", "")).strip()
                if not text:
                    raise ProviderResponseError(
                        "OpenAI response did not contain text output"
                    )
                usage = _usage_from_response(response)
                _set_span_result(span, response, usage)
                return ChatResult(text=text, model=model, usage=usage)
        except OpenAIProviderError:
            raise

    async def _structured_attempt(
        self,
        messages: Sequence[ChatMessage],
        schema: type[SchemaT],
        *,
        model: str,
        attempt: int,
    ) -> SchemaT:
        payload = [message.model_dump(mode="json") for message in messages]
        async with self._tracker.llm_span(
            model,
            {
                "provider": "openai",
                "operation": "structured_output",
                "schema": schema.__name__,
                "attempt": attempt,
                "message_count": len(payload),
            },
        ) as span:
            try:
                response = await self._client.responses.parse(
                    model=model,
                    input=payload,
                    text_format=schema,
                    temperature=self._config.temperature,
                    max_output_tokens=self._config.max_tokens,
                )
            except (APITimeoutError, RateLimitError, APIStatusError) as error:
                _raise_provider_error(error)
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
        model = self._config.model_for(agent_name)
        current_messages = list(messages)

        for attempt in (1, 2):
            try:
                return await self._structured_attempt(
                    current_messages, schema, model=model, attempt=attempt
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
