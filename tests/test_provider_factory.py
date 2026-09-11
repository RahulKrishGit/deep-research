"""Tests for the explicit chat-provider factory selection."""

import pytest

import deep_research.providers.factory as factory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.providers import (
    DeepSeekChatProvider,
    OpenAIChatProvider,
    ProviderConfigurationError,
    build_chat_provider,
)
from deep_research.providers.deepseek_provider import DeepSeekJudgeProvider
from deep_research.utils.config import LLMConfig


@pytest.fixture
def tracker() -> Tracker:
    return Tracker(
        LangSmithRuntimeConfig(
            tracing_enabled=False,
            project="factory-tests",
            api_key=None,
        )
    )


@pytest.mark.parametrize(
    ("provider_name", "model", "expected"),
    [
        ("deepseek", "deepseek-v4-flash", DeepSeekChatProvider),
        ("openai", "gpt-4o", OpenAIChatProvider),
    ],
)
def test_factory_builds_exactly_the_selected_adapter(
    provider_name, model, expected, tracker, monkeypatch
) -> None:
    built: list[type[object]] = []

    class RecordingAdapter:
        def __init__(self, config, received_tracker, *, api_key=None):
            built.append(expected)
            assert config.provider == provider_name
            assert received_tracker is tracker
            assert api_key is None

    monkeypatch.setattr(factory, expected.__name__, RecordingAdapter)
    config = LLMConfig(
        provider=provider_name,
        model=model,
        thinking_mode="disabled" if provider_name == "openai" else "enabled",
        reasoning_effort="none" if provider_name == "openai" else "high",
    )

    result = build_chat_provider(config, tracker)

    assert isinstance(result, RecordingAdapter)
    assert built == [expected]


@pytest.mark.parametrize(
    ("provider_name", "model", "expected"),
    [
        ("deepseek", "deepseek-v4-flash", DeepSeekJudgeProvider),
        ("openai", "gpt-4o", OpenAIChatProvider),
    ],
)
def test_judge_factory_builds_the_selected_judge_adapter(
    provider_name, model, expected, tracker, monkeypatch
) -> None:
    built: list[tuple[str, str | None]] = []
    recorders: dict[str, type[object]] = {}

    def make_recording_adapter(adapter_name: str):
        class RecordingAdapter:
            def __init__(self, config, received_tracker, *, api_key=None):
                assert config.provider == provider_name
                assert received_tracker is tracker
                built.append((adapter_name, api_key))

        recorders[adapter_name] = RecordingAdapter
        return RecordingAdapter

    monkeypatch.setattr(
        factory,
        "DeepSeekJudgeProvider",
        make_recording_adapter("DeepSeekJudgeProvider"),
        raising=False,
    )
    monkeypatch.setattr(
        factory,
        "OpenAIChatProvider",
        make_recording_adapter("OpenAIChatProvider"),
    )
    config = LLMConfig(
        provider=provider_name,
        model=model,
        thinking_mode="disabled" if provider_name == "openai" else "enabled",
        reasoning_effort="none" if provider_name == "openai" else "high",
    )

    result = factory.build_judge_provider(
        config, tracker, api_key="explicit-judge-key"
    )

    expected_name = expected.__name__
    assert isinstance(result, recorders[expected_name])
    assert built == [(expected_name, "explicit-judge-key")]


class MinimalLLMConfig:
    """Bypass Pydantic so an unregistered provider value can be tested."""

    def __init__(self, provider: str) -> None:
        self.provider = provider


def test_unknown_provider_is_rejected_without_fallback(tracker) -> None:
    with pytest.raises(ProviderConfigurationError) as caught:
        build_chat_provider(MinimalLLMConfig("other"), tracker)

    message = str(caught.value)
    assert "other" in message
    assert "deepseek" in message
    assert "openai" in message


def test_judge_factory_rejects_unknown_provider_without_fallback(
    tracker, monkeypatch
) -> None:
    built: list[str] = []

    class UnexpectedAdapter:
        def __init__(self, *args, **kwargs):
            built.append("constructed")

    monkeypatch.setattr(
        factory, "DeepSeekJudgeProvider", UnexpectedAdapter, raising=False
    )
    monkeypatch.setattr(factory, "OpenAIChatProvider", UnexpectedAdapter)

    with pytest.raises(ProviderConfigurationError) as caught:
        factory.build_judge_provider(
            MinimalLLMConfig("other"),
            tracker,
            api_key="explicit-judge-key",
        )

    message = str(caught.value)
    assert "other" in message
    assert "deepseek" in message
    assert "openai" in message
    assert built == []


def test_public_judge_symbols_are_reexported() -> None:
    import deep_research.providers as providers

    assert providers.DeepSeekJudgeProvider is DeepSeekJudgeProvider
    assert providers.JudgeAdapter is factory.JudgeAdapter
    assert providers.build_judge_provider is factory.build_judge_provider


def test_build_embedding_provider_selects_the_local_model() -> None:
    from deep_research.providers import (
        LocalEmbeddingProvider,
        build_embedding_provider,
    )

    provider = build_embedding_provider("local")

    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.dimension == 384


def test_build_embedding_provider_selects_openai_with_the_configured_model() -> None:
    from deep_research.providers import (
        OpenAIEmbeddingProvider,
        build_embedding_provider,
    )

    provider = build_embedding_provider("openai", model="text-embedding-3-large")

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.model == "text-embedding-3-large"


def test_build_embedding_provider_rejects_an_unknown_name_without_falling_back() -> (
    None
):
    from deep_research.providers import (
        ProviderConfigurationError,
        build_embedding_provider,
    )

    with pytest.raises(ProviderConfigurationError) as caught:
        build_embedding_provider("cohere")

    assert "local, openai" in str(caught.value)


def test_build_chat_provider_passes_an_explicit_key_through(tracker) -> None:
    """Callers holding credentials as data must not need the process env."""
    import os

    from deep_research.providers import build_chat_provider
    from deep_research.utils.config import LLMConfig

    previous = os.environ.pop("DEEPSEEK_API_KEY", None)
    try:
        provider = build_chat_provider(
            LLMConfig(), tracker, api_key="sk-deepseek-abcdefgh"
        )
    finally:
        if previous is not None:
            os.environ["DEEPSEEK_API_KEY"] = previous

    assert isinstance(provider, DeepSeekChatProvider)
    # the key came from the argument, not the (popped) environment
    #
    # DeepSeekChatProvider does not keep the raw string on an ``_api_key``
    # attribute -- it resolves the key immediately into a real ``AsyncOpenAI``
    # client (see ``_build_client``/``__init__`` in
    # ``deep_research/providers/deepseek_provider.py``) and keeps only that
    # client, on ``_client``. The openai SDK's client stores the key it was
    # constructed with on its own ``.api_key`` attribute, so that is what
    # proves the explicit key -- and not the popped environment variable --
    # reached the adapter.
    assert provider._client.api_key == "sk-deepseek-abcdefgh"
