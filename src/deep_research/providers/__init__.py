"""Selectable chat providers (DeepSeek, OpenAI) and OpenAI embeddings."""

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
from deep_research.providers.deepseek_provider import (
    DEEPSEEK_BASE_URL,
    DeepSeekChatProvider,
)
from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    LOCAL_EMBEDDING_DIMENSION,
    LOCAL_EMBEDDING_PROVIDER,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from deep_research.providers.factory import (
    ChatAdapter,
    EmbeddingAdapter,
    build_chat_provider,
    build_embedding_provider,
    validate_agent_model_configs,
)
from deep_research.providers.openai_provider import OpenAIChatProvider

__all__ = [
    "ChatAdapter",
    "ChatMessage",
    "ChatResult",
    "DEEPSEEK_BASE_URL",
    "DEFAULT_EMBEDDING_MODEL",
    "DeepSeekChatProvider",
    "EmbeddingAdapter",
    "LOCAL_EMBEDDING_DIMENSION",
    "LOCAL_EMBEDDING_PROVIDER",
    "LocalEmbeddingProvider",
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
    "build_chat_provider",
    "build_embedding_provider",
    "capability_for",
    "resolve_request_settings",
    "validate_agent_model_configs",
]
