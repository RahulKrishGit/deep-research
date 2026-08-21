"""Anchored, fail-closed embedding model capability registry.

Every embedding model whose *name* actually selects something is registered
here as frozen metadata, and the resolver validates a provider's embedding
model against it before any live embedding request; nothing falls through to
a provider request unvalidated. The local provider is deliberately absent --
see ``embedding_capability_for`` for why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

from deep_research.providers.contracts import ProviderConfigurationError
from deep_research.providers.embeddings import LOCAL_EMBEDDING_PROVIDER
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
) -> EmbeddingModelCapability | None:
    """Return the capability whose anchored pattern matches ``model``.

    ``local`` is not in the registry and never will be: ``LocalEmbeddingProvider``
    takes no model argument at all (``build_embedding_provider`` says so), so
    there is exactly one local model, chosen by the adapter, with a fixed
    vector width -- no model *name* ever selects it. That makes every local
    model string, including the shipped default, equally inert: there is no
    such thing as a mismatched one to catch, so nothing is validated here for
    it and ``None`` is returned without a registry lookup. Do not
    "helpfully" re-add a local entry -- any pattern for it would have to
    assert a fake fact (a name the adapter cannot act on) just to keep the
    matching machinery happy.

    Raises:
        ProviderConfigurationError: If ``provider`` is ``"openai"`` and
            ``model`` is not registered, so an unknown or typo'd embedding
            model never reaches a live embedding request.
    """
    if provider == LOCAL_EMBEDDING_PROVIDER:
        return None
    entries = _EMBEDDING_CAPABILITIES.get(provider)
    if entries is None:
        supported = _accepted_text(
            frozenset(_EMBEDDING_CAPABILITIES) | {LOCAL_EMBEDDING_PROVIDER}
        )
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
