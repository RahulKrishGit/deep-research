import pytest

from deep_research.providers import (
    EmbeddingModelCapability,
    ProviderConfigurationError,
    embedding_capability_for,
)


@pytest.mark.parametrize(
    ("provider", "model", "dimension"),
    [
        ("openai", "text-embedding-3-small", 1536),
        ("openai", "text-embedding-3-large", 3072),
    ],
)
def test_embedding_capabilities_resolve_dimensions(
    provider: str, model: str, dimension: int
) -> None:
    capability = embedding_capability_for(provider, model)

    assert capability is not None
    assert capability.dimension == dimension
    assert isinstance(capability, EmbeddingModelCapability)


@pytest.mark.parametrize(
    "model",
    [
        "text-embedding-3-small",
        "text-embedding-3-large",
        # A real OpenAI model name that would be wrong for the OpenAI
        # provider is still fine here: for ``local`` there is exactly one
        # model, chosen by the adapter (``LocalEmbeddingProvider`` takes no
        # model argument at all), with a fixed vector width. No model name
        # ever selects it, so no name can ever be a *mismatched* one --
        # every string, including nonsense, is equally inert and passes.
        "not-a-real-model",
        "",
    ],
)
def test_the_local_provider_accepts_any_model_name_because_none_select_anything(
    model: str,
) -> None:
    """``local``'s embedding model string is inert, so validation for it is
    a deliberate no-op: this replaces the deleted
    ``test_an_unknown_embedding_model_fails_closed_by_name``'s ``local``
    case and the deleted
    ``test_a_live_run_with_an_openai_model_name_for_local_embeddings_fails_closed``,
    both of which rejected a local model name -- that rejection was the
    design error this correction fixes. Do not restore it."""
    assert embedding_capability_for("local", model) is None


def test_an_unknown_openai_embedding_model_fails_closed_by_name() -> None:
    # A real OpenAI model name the project still knows a dimension for
    # (embeddings.py's _KNOWN_DIMENSIONS) but never selects anywhere.
    with pytest.raises(ProviderConfigurationError) as caught:
        embedding_capability_for("openai", "text-embedding-ada-002")

    message = str(caught.value)
    assert "openai" in message
    assert "text-embedding-ada-002" in message
    assert "API_KEY" not in message


def test_an_unsupported_embedding_provider_names_the_supported_list() -> None:
    with pytest.raises(ProviderConfigurationError) as caught:
        embedding_capability_for("anthropic", "text-embedding-3-small")

    message = str(caught.value)
    assert "anthropic" in message
    assert "local" in message
    assert "openai" in message
    assert "API_KEY" not in message


def test_embedding_patterns_are_anchored() -> None:
    """A superstring must never match via an unanchored pattern."""
    with pytest.raises(ProviderConfigurationError, match="embedding model"):
        embedding_capability_for("openai", "text-embedding-3-small-x")


def test_embedding_errors_never_mention_secret_shaped_content() -> None:
    with pytest.raises(ProviderConfigurationError) as caught:
        embedding_capability_for("openai", "text-embedding-3-small-typo")

    assert "API_KEY" not in str(caught.value)
