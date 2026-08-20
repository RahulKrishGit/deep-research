"""Anchored, fail-closed embedding model capability registry.

Every supported embedding model is registered here as frozen metadata. The
resolver validates a provider's embedding model before any live embedding
request; nothing falls through to a provider request unvalidated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

from deep_research.providers.contracts import ProviderConfigurationError
from deep_research.providers.embeddings import LOCAL_EMBEDDING_DIMENSION
from deep_research.utils.config import EmbeddingProviderName


@dataclass(frozen=True)
class EmbeddingModelCapability:
    """Frozen metadata for one anchored embedding model."""

    pattern: Pattern[str]
    dimension: int


def _capability(pattern: str, dimension: int) -> EmbeddingModelCapability:
    """Build one registry entry from an anchored pattern and its vector width."""
    return EmbeddingModelCapability(pattern=re.compile(pattern), dimension=dimension)


_EMBEDDING_CAPABILITIES: dict[
    EmbeddingProviderName, tuple[EmbeddingModelCapability, ...]
] = {
    "local": (
        # Why does the local provider accept an OpenAI-shaped model name?
        # ``LLMConfig.embedding_model`` is a single string field shared by
        # both embedding providers, and its default is
        # ``text-embedding-3-small`` regardless of ``embedding_provider``;
        # the string is genuinely inert for local (the adapter takes no
        # model name at all, ``build_embedding_provider`` says so). Accepting
        # exactly the shipped default here keeps the default stack (local
        # provider + inherited default model string) validating while still
        # catching a real misconfiguration -- any *other* explicit model
        # name under ``embedding_provider == "local"`` is rejected, because
        # nothing can ever put it to use.
        _capability(r"^text-embedding-3-small$", LOCAL_EMBEDDING_DIMENSION),
    ),
    "openai": (
        _capability(r"^text-embedding-3-small$", 1536),
        _capability(r"^text-embedding-3-large$", 3072),
    ),
}


def _accepted_text(values: frozenset[str]) -> str:
    """Render accepted values as sorted, comma-separated text."""
    return ", ".join(sorted(values))


def embedding_capability_for(
    provider: EmbeddingProviderName, model: str
) -> EmbeddingModelCapability:
    """Return the capability whose anchored pattern matches ``model``.

    Raises:
        ProviderConfigurationError: If the provider or model is not
            registered, so an unknown or typo'd embedding model never
            reaches a live embedding request.
    """
    entries = _EMBEDDING_CAPABILITIES.get(provider)
    if entries is None:
        supported = _accepted_text(frozenset(_EMBEDDING_CAPABILITIES))
        raise ProviderConfigurationError(
            f"Provider '{provider}' is not supported; "
            f"supported providers: {supported}"
        )
    for capability in entries:
        if capability.pattern.fullmatch(model):
            return capability
    supported = _accepted_text(
        frozenset(capability.pattern.pattern for capability in entries)
    )
    raise ProviderConfigurationError(
        f"Provider '{provider}' does not support embedding model '{model}'; "
        f"supported embedding models: {supported}"
    )
