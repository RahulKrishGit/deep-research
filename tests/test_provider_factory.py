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
        def __init__(self, config, received_tracker):
            built.append(expected)
            assert config.provider == provider_name
            assert received_tracker is tracker

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
