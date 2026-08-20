import pytest

from deep_research.providers import (
    EmbeddingModelCapability,
    ProviderConfigurationError,
    embedding_capability_for,
)
from deep_research.providers.embeddings import LOCAL_EMBEDDING_DIMENSION


@pytest.mark.parametrize(
    ("provider", "model", "dimension"),
    [
        ("local", "text-embedding-3-small", LOCAL_EMBEDDING_DIMENSION),
        ("openai", "text-embedding-3-small", 1536),
        ("openai", "text-embedding-3-large", 3072),
    ],
)
def test_embedding_capabilities_resolve_dimensions(
    provider: str, model: str, dimension: int
) -> None:
    capability = embedding_capability_for(provider, model)

    assert capability.dimension == dimension
    assert isinstance(capability, EmbeddingModelCapability)


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        # A real OpenAI model name that is wrong for the local provider:
        # the local adapter takes no model name at all, so nothing can
        # ever put this one to use.
        ("local", "text-embedding-3-large"),
        # A real OpenAI model name the project still knows a dimension for
        # (embeddings.py's _KNOWN_DIMENSIONS) but never selects anywhere.
        ("openai", "text-embedding-ada-002"),
    ],
)
def test_an_unknown_embedding_model_fails_closed_by_name(
    provider: str, model: str
) -> None:
    with pytest.raises(ProviderConfigurationError) as caught:
        embedding_capability_for(provider, model)

    message = str(caught.value)
    assert provider in message
    assert model in message
    assert "API_KEY" not in message


def test_an_unsupported_embedding_provider_names_the_supported_list() -> None:
    with pytest.raises(ProviderConfigurationError) as caught:
        embedding_capability_for("anthropic", "text-embedding-3-small")

    message = str(caught.value)
    assert "anthropic" in message
    assert "local" in message
    assert "openai" in message
    assert "API_KEY" not in message


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("local", "text-embedding-3-small-x"),
        ("openai", "text-embedding-3-small-x"),
    ],
)
def test_embedding_patterns_are_anchored(provider: str, model: str) -> None:
    """A superstring must never match via an unanchored pattern."""
    with pytest.raises(ProviderConfigurationError, match="embedding model"):
        embedding_capability_for(provider, model)


def test_embedding_errors_never_mention_secret_shaped_content() -> None:
    with pytest.raises(ProviderConfigurationError) as caught:
        embedding_capability_for("openai", "text-embedding-3-small-typo")

    assert "API_KEY" not in str(caught.value)
