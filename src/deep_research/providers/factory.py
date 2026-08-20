"""Explicit chat-provider selection with no inference and no fallback.

The only place the configured provider name selects a chat adapter. There
is no model-name inference, no key lookup, and no cross-provider fallback:
an unknown provider is a ``ProviderConfigurationError``, and a selected
provider that fails to construct stays failed — the other provider is
never constructed in its place.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

from deep_research.observability import Tracker
from deep_research.providers.capabilities import (
    ResolvedRequestSettings,
    resolve_request_settings,
)
from deep_research.providers.contracts import ProviderConfigurationError
from deep_research.providers.deepseek_provider import DeepSeekChatProvider
from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    LOCAL_EMBEDDING_PROVIDER,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from deep_research.providers.openai_provider import OpenAIChatProvider
from deep_research.utils.config import EmbeddingProviderName, LLMConfig

ChatAdapter: TypeAlias = OpenAIChatProvider | DeepSeekChatProvider
EmbeddingAdapter: TypeAlias = LocalEmbeddingProvider | OpenAIEmbeddingProvider


def build_chat_provider(config: LLMConfig, tracker: Tracker) -> ChatAdapter:
    if config.provider == "deepseek":
        return DeepSeekChatProvider(config, tracker)
    if config.provider == "openai":
        return OpenAIChatProvider(config, tracker)
    raise ProviderConfigurationError(
        f"Unsupported chat provider {config.provider!r}; "
        "accepted values: deepseek, openai"
    )


def build_embedding_provider(
    provider: EmbeddingProviderName,
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> EmbeddingAdapter:
    """Select the embedding adapter by name, with no inference or fallback.

    ``model`` applies to the OpenAI adapter only: the local adapter has
    exactly one model and a fixed vector width, so naming a model for it
    would be a setting that cannot take effect.
    """
    if provider == LOCAL_EMBEDDING_PROVIDER:
        return LocalEmbeddingProvider()
    if provider == "openai":
        return OpenAIEmbeddingProvider(model=model)
    raise ProviderConfigurationError(
        f"Unsupported embedding provider {provider!r}; "
        "accepted values: local, openai"
    )


def validate_agent_model_configs(
    config: LLMConfig, agent_names: Sequence[str]
) -> dict[str, ResolvedRequestSettings]:
    """Resolve and validate the effective model of every agent, once each.

    Runs before any memory, tool, provider, or graph construction so an
    unsupported model, thinking mode, or reasoning effort for any of the
    six agents fails the run before a single collaborator exists. Each
    agent is resolved exactly once and the validated settings are returned
    in agent order, so a caller can see exactly what the run would send.
    """
    resolved: dict[str, ResolvedRequestSettings] = {}
    for name in agent_names:
        effective = config.resolve_for(name)
        try:
            resolved[name] = resolve_request_settings(
                config.provider, effective
            )
        except ProviderConfigurationError as error:
            # The capability message names only provider/model/setting
            # values; the agent name is the one missing context worth
            # adding. The serialized override object is never included.
            raise ProviderConfigurationError(
                f"agent {name!r}: {error}"
            ) from error
    return resolved
