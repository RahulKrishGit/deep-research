"""Model and embedding providers. OpenAI only in the first build."""

from deep_research.providers.capabilities import (
    ModelCapability,
    ResolvedRequestSettings,
    capability_for,
    resolve_request_settings,
)
from deep_research.providers.contracts import (
    ChatMessage,
    ChatResult,
    OpenAIProviderError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)
from deep_research.providers.openai_provider import OpenAIChatProvider

__all__ = [
    "ChatMessage",
    "ChatResult",
    "DEFAULT_EMBEDDING_MODEL",
    "ModelCapability",
    "OpenAIChatProvider",
    "OpenAIEmbeddingProvider",
    "OpenAIProviderError",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ResolvedRequestSettings",
    "StructuredOutputError",
    "capability_for",
    "resolve_request_settings",
]
