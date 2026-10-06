"""Tests for the explicit chat-provider factory selection."""

from types import SimpleNamespace

import pytest

import deep_research.providers.factory as factory
from deep_research.providers import (
    ChatMessage,
    OpenAIChatProvider,
    ProviderConfigurationError,
    build_chat_provider,
)
from deep_research.providers.deepseek_provider import (
    DeepSeekJudgeProvider,
    DeepSeekSchemaChatProvider,
)
from deep_research.utils.config import LLMConfig


@pytest.mark.parametrize(
    ("provider_name", "model", "expected"),
    [
        ("deepseek", "deepseek-v4-flash", DeepSeekSchemaChatProvider),
        ("openai", "gpt-4o", OpenAIChatProvider),
    ],
)
def test_factory_builds_exactly_the_selected_adapter(
    provider_name, model, expected, tracker, monkeypatch
) -> None:
    built: list[type[object]] = []

    class RecordingAdapter:
        def __init__(
            self,
            config,
            received_tracker,
            *,
            api_key=None,
            request_budget=None,
            telemetry=None,
        ):
            built.append(expected)
            assert config.provider == provider_name
            assert received_tracker is tracker
            assert api_key is None
            # No budget was supplied by this caller, so the adapter's own
            # uncounted default applies unchanged.
            assert request_budget is None
            # No collector either: the adapter records into a private no-op.
            assert telemetry is None

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
            def __init__(
                self,
                config,
                received_tracker,
                *,
                api_key=None,
                request_budget=None,
                telemetry=None,
            ):
                assert config.provider == provider_name
                assert received_tracker is tracker
                assert request_budget is None
                assert telemetry is None
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


def test_public_target_schema_symbol_is_reexported() -> None:
    import deep_research.providers as providers

    assert (
        providers.DeepSeekSchemaChatProvider is DeepSeekSchemaChatProvider
    )
    assert providers.ChatAdapter is factory.ChatAdapter


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

    assert isinstance(provider, DeepSeekSchemaChatProvider)
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


# ---------------------------------------------------------------------------
# Request-budget plumbing: the factory is the only place a chat or judge
# adapter is selected, so it is also the only place a run's attempt budget can
# reach the adapter that has to reserve against it.
# ---------------------------------------------------------------------------


def test_request_budget_reaches_the_deepseek_chat_adapter(tracker) -> None:
    from deep_research.request_budget import RequestBudget
    from deep_research.utils.config import LLMConfig

    budget = RequestBudget()

    provider = build_chat_provider(
        LLMConfig(), tracker, api_key="sk-deepseek-abcdefgh", request_budget=budget
    )

    assert provider._request_budget is budget


def test_request_budget_reaches_the_deepseek_judge_adapter(tracker) -> None:
    from deep_research.request_budget import RequestBudget
    from deep_research.utils.config import LLMConfig

    budget = RequestBudget()

    provider = factory.build_judge_provider(
        LLMConfig(), tracker, api_key="sk-deepseek-abcdefgh", request_budget=budget
    )

    assert provider._request_budget is budget


def test_request_budget_defaults_to_none_in_both_deepseek_factories(tracker) -> None:
    """Every existing caller keeps exactly today's uncounted behaviour."""
    from deep_research.utils.config import LLMConfig

    chat = build_chat_provider(
        LLMConfig(), tracker, api_key="sk-deepseek-abcdefgh"
    )
    judge = factory.build_judge_provider(
        LLMConfig(), tracker, api_key="sk-deepseek-abcdefgh"
    )

    assert chat._request_budget is None
    assert judge._request_budget is None


def _openai_config() -> LLMConfig:
    return LLMConfig(
        provider="openai",
        model="gpt-4o",
        thinking_mode="disabled",
        reasoning_effort="none",
    )


def test_request_budget_reaches_the_openai_chat_adapter(tracker) -> None:
    """The OpenAI transport reserves against the run budget, so the factory
    hands it over instead of refusing it: a refused budget would leave the
    OpenAI half of a run uncounted."""
    from deep_research.request_budget import RequestBudget

    budget = RequestBudget()

    provider = build_chat_provider(
        _openai_config(),
        tracker,
        api_key="sk-openai-abcdefgh",
        request_budget=budget,
    )

    assert isinstance(provider, OpenAIChatProvider)
    assert provider._request_budget is budget


def test_request_budget_reaches_the_openai_judge_adapter(tracker) -> None:
    from deep_research.request_budget import RequestBudget

    budget = RequestBudget()

    provider = factory.build_judge_provider(
        _openai_config(),
        tracker,
        api_key="sk-openai-abcdefgh",
        request_budget=budget,
    )

    assert isinstance(provider, OpenAIChatProvider)
    assert provider._request_budget is budget


def test_request_budget_defaults_to_none_in_both_openai_factories(tracker) -> None:
    """A ``None`` budget stays exactly today's uncounted behaviour."""
    chat = build_chat_provider(
        _openai_config(), tracker, api_key="sk-openai-abcdefgh"
    )
    judge = factory.build_judge_provider(
        _openai_config(), tracker, api_key="sk-openai-abcdefgh"
    )

    assert chat._request_budget is None
    assert judge._request_budget is None


# ---------------------------------------------------------------------------
# Telemetry plumbing: the run's collector reaches the adapter the same way the
# run's attempt budget does, so every provider call of the run reports to the
# one object the artefacts are rendered from.
# ---------------------------------------------------------------------------


class _FakeAsyncStream:
    """Minimal async stream double for Chat Completions."""
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = list(chunks)

    async def __aenter__(self) -> "_FakeAsyncStream":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


def _chat_stream_chunks(response: object) -> list[SimpleNamespace]:
    """Rebuild one Chat Completions response as stream chunks."""
    choice = response.choices[0]
    message = choice.message
    response_id = getattr(response, "id", None)
    model = getattr(response, "model", None)

    def _chunk(*, delta: SimpleNamespace | None, finish_reason, usage) -> SimpleNamespace:
        choices = (
            []
            if delta is None and finish_reason is None
            else [SimpleNamespace(delta=delta, finish_reason=finish_reason)]
        )
        return SimpleNamespace(id=response_id, model=model, choices=choices, usage=usage)

    return [
        _chunk(
            delta=SimpleNamespace(
                content=getattr(message, "content", None),
                reasoning_content=getattr(message, "reasoning_content", None),
                tool_calls=None,
            ),
            finish_reason=None,
            usage=None,
        ),
        _chunk(
            delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=None),
            finish_reason=getattr(choice, "finish_reason", None),
            usage=None,
        ),
        _chunk(delta=None, finish_reason=None, usage=getattr(response, "usage", None)),
    ]


class _ScriptedCompletions:
    """A Chat Completions endpoint that answers with one scripted response."""

    def __init__(self, response: object) -> None:
        self._response = response

    async def create(self, **kwargs: object) -> object:
        # If streaming is requested, wrap response in stream
        if kwargs.get("stream"):
            return _FakeAsyncStream(_chat_stream_chunks(self._response))
        return self._response


def _scripted_client(response: object) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(completions=_ScriptedCompletions(response))
    )


def _chat_response(
    *, text: str = "answer", finish_reason: str = "stop", completion_tokens: int = 2
) -> SimpleNamespace:
    return SimpleNamespace(
        id="factory-response",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=text, reasoning_content=None, tool_calls=None
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=4,
            completion_tokens=completion_tokens,
            total_tokens=4 + completion_tokens,
        ),
    )


@pytest.mark.asyncio
async def test_telemetry_reaches_the_deepseek_chat_adapter(tracker) -> None:
    """A collector passed to the factory receives a scripted call's record."""
    from deep_research.observability import RunTelemetryCollector
    from deep_research.utils.config import LLMConfig

    collector = RunTelemetryCollector()
    config = LLMConfig()
    provider = build_chat_provider(
        config, tracker, api_key="sk-deepseek-abcdefgh", telemetry=collector
    )

    assert provider._telemetry is collector
    assert (
        factory.build_judge_provider(
            config, tracker, api_key="sk-deepseek-abcdefgh", telemetry=collector
        )._telemetry
        is collector
    )

    provider._client = _scripted_client(_chat_response(completion_tokens=2))
    async with tracker.session_span("factory-telemetry", "review"):
        result = await provider.complete(
            [ChatMessage(role="user", content="prompt")], agent_name="researcher"
        )

    assert result.usage.output_tokens == 2
    [stage] = collector.snapshot().stages
    assert (stage.agent, stage.calls) == ("researcher", 1)
    [operation] = stage.operations
    assert operation.agent == "researcher"
    assert operation.max_output_tokens == 2
    assert operation.configured_cap == config.max_tokens
    assert operation.cap_key == "llm.max_tokens"
    assert operation.truncations == 0


@pytest.mark.asyncio
async def test_a_truncated_call_reaches_the_collector_as_a_truncation(
    tracker,
) -> None:
    """An output-limit response is the truncation count the run telemetry reports.

    The provider raises on the truncated reply *and* has to report it: the
    call really happened, and a truncation that never reaches the collector
    would leave the one figure an operator acts on permanently at zero.
    """
    from deep_research.observability import RunTelemetryCollector
    from deep_research.providers.deepseek_provider import ProviderOutputLimitError
    from deep_research.utils.config import LLMConfig

    collector = RunTelemetryCollector()
    config = LLMConfig()
    provider = build_chat_provider(
        config, tracker, api_key="sk-deepseek-abcdefgh", telemetry=collector
    )
    provider._client = _scripted_client(_chat_response(finish_reason="length"))

    async with tracker.session_span("factory-telemetry", "review"):
        with pytest.raises(ProviderOutputLimitError):
            await provider.complete(
                [ChatMessage(role="user", content="prompt")],
                agent_name="report_writer",
            )

    [stage] = collector.snapshot().stages
    [operation] = stage.operations
    assert operation.truncations == 1
    assert operation.configured_cap == config.max_tokens
