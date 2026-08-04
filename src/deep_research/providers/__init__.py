"""Model and embedding providers with OpenAI-compatible adapters."""

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
    OpenAIEmbeddingProvider,
)
from deep_research.providers.factory import (
    ChatAdapter,
    build_chat_provider,
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
    "capability_for",
    "resolve_request_settings",
    "validate_agent_model_configs",
]
