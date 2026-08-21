from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from deep_research.observability import TokenUsage

MessageRole = Literal["developer", "system", "user", "assistant"]


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatMessage(ProviderContract):
    role: MessageRole
    content: str = Field(min_length=1)


class ChatResult(ProviderContract):
    text: str = Field(min_length=1)
    model: str = Field(min_length=1)
    usage: TokenUsage


class ProviderError(RuntimeError):
    """Base caller-facing error for every chat provider boundary."""


class ProviderConfigurationError(ProviderError):
    """The selected provider or effective model configuration is invalid."""


class ProviderTimeoutError(ProviderError):
    """A provider request exceeded its timeout."""


class ProviderRateLimitError(ProviderError):
    """A provider rejected a request due to rate limiting."""


class ProviderResponseError(ProviderError):
    """A provider returned an unusable response or status error."""


class StructuredOutputError(ProviderError):
    """Structured output remained invalid after one repair request."""


OpenAIProviderError = ProviderError
