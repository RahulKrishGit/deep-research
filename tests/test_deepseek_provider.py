"""Unit tests for the project-owned DeepSeek chat provider boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAIError,
    RateLimitError,
)

import deep_research.providers.deepseek_provider as deepseek_module
from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers.deepseek_provider import (
    DEEPSEEK_BASE_URL,
    ChatMessage,
    DeepSeekChatProvider,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from deep_research.utils.config import LLMConfig


class RecordingCompletions:
    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeDeepSeekClient:
    def __init__(self, completions: RecordingCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def chat_response(
    *,
    text: object = "answer",
    finish_reason: str = "stop",
    prompt_tokens: object = 4,
    completion_tokens: object = 2,
    reasoning_content: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="deepseek-response",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=text, reasoning_content=reasoning_content
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(
                prompt_tokens + completion_tokens
                if isinstance(prompt_tokens, int)
                and not isinstance(prompt_tokens, bool)
                and isinstance(completion_tokens, int)
                and not isinstance(completion_tokens, bool)
                else None
            ),
        ),
    )


class CapturingTracker(Tracker):
    def __init__(self) -> None:
        super().__init__(LangSmithRuntimeConfig(tracing_enabled=False))
        self.llm_inputs: list[dict[str, object]] = []

    def llm_span(self, model, inputs):
        self.llm_inputs.append(dict(inputs))
        return super().llm_span(model, inputs)


def local_tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


def deepseek_config(**updates: object) -> LLMConfig:
    return LLMConfig.model_validate(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            **updates,
        }
    )


def test_deepseek_requires_key_without_injected_client(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekChatProvider(deepseek_config(), local_tracker())


def test_explicit_blank_deepseek_key_does_not_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    with pytest.raises(ProviderConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekChatProvider(deepseek_config(), local_tracker(), api_key="")


def test_deepseek_builds_openai_compatible_client_with_code_owned_url(
    monkeypatch,
) -> None:
    constructed: list[dict[str, object]] = []

    class RecordingAsyncOpenAI:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    monkeypatch.setattr(
        deepseek_module._openai_errors(), "AsyncOpenAI", RecordingAsyncOpenAI
    )
    DeepSeekChatProvider(deepseek_config(), local_tracker(), api_key="deepseek-key")

    assert constructed == [
        {
            "api_key": "deepseek-key",
            "base_url": DEEPSEEK_BASE_URL,
            "timeout": 60.0,
            "max_retries": 2,
        }
    ]


def test_deepseek_injected_client_requires_no_key_and_is_retained(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    injected = FakeDeepSeekClient(RecordingCompletions())
    provider = DeepSeekChatProvider(deepseek_config(), local_tracker(), client=injected)
    assert provider._client is injected


@pytest.mark.asyncio
async def test_deepseek_plain_completion_translates_roles_and_thinking() -> None:
    completions = RecordingCompletions(
        chat_response(text="  concise answer  ", prompt_tokens=8, completion_tokens=3)
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [
                ChatMessage(role="developer", content="policy"),
                ChatMessage(role="user", content="question"),
            ],
            agent_name="planner",
        )

    assert result.text == "concise answer"
    assert result.model == "deepseek-v4-flash"
    assert result.usage.model_dump() == {
        "input_tokens": 8,
        "output_tokens": 3,
        "total_tokens": 11,
    }
    call = completions.calls[0]
    assert call["messages"] == [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "question"},
    ]
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    assert call["max_tokens"] == 4096
    assert "temperature" not in call


@pytest.mark.asyncio
async def test_deepseek_disabled_thinking_omits_effort_and_sends_temperature() -> None:
    completions = RecordingCompletions(chat_response(text="answer"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(
            thinking_mode="disabled", reasoning_effort="max", temperature=0.2
        ),
        tracker,
        client=FakeDeepSeekClient(completions),
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete([ChatMessage(role="user", content="question")])

    call = completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in call
    assert call["temperature"] == 0.2


@pytest.mark.asyncio
async def test_deepseek_translation_preserves_system_user_assistant_order() -> None:
    completions = RecordingCompletions(chat_response(text="answer"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        await provider.complete(
            [
                ChatMessage(role="system", content="first system"),
                ChatMessage(role="user", content="second user"),
                ChatMessage(role="assistant", content="third assistant"),
                ChatMessage(role="user", content="fourth user"),
            ]
        )

    assert completions.calls[0]["messages"] == [
        {"role": "system", "content": "first system"},
        {"role": "user", "content": "second user"},
        {"role": "assistant", "content": "third assistant"},
        {"role": "user", "content": "fourth user"},
    ]


@pytest.mark.asyncio
async def test_deepseek_absent_usage_maps_to_zero_tokens() -> None:
    completions = RecordingCompletions(
        SimpleNamespace(
            id="deepseek-response",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="answer"),
                )
            ],
        )
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [ChatMessage(role="user", content="question")]
        )

    assert result.usage.model_dump() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        SimpleNamespace(prompt_tokens=True, completion_tokens=2),
        SimpleNamespace(prompt_tokens="8", completion_tokens=2),
        SimpleNamespace(prompt_tokens=8, completion_tokens=-1),
        SimpleNamespace(prompt_tokens=None, completion_tokens=2),
        SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens=5),
        SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens="11"),
        SimpleNamespace(prompt_tokens=8, completion_tokens=True),
    ],
)
async def test_deepseek_rejects_malformed_usage(usage: SimpleNamespace) -> None:
    response = SimpleNamespace(
        id="deepseek-response",
        choices=[
            SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content="answer")
            )
        ],
        usage=usage,
    )
    completions = RecordingCompletions(response)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="malformed usage"):
            await provider.complete([ChatMessage(role="user", content="question")])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("malformed_response", "sensitive_text"),
    [
        (SimpleNamespace(), "answer"),
        (SimpleNamespace(choices=[]), "answer"),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="first"),
                    ),
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="second"),
                    ),
                ]
            ),
            "second",
        ),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop")]), "answer"),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=None),
                    )
                ]
            ),
            "answer",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="   "),
                    )
                ]
            ),
            "   ",
        ),
        (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=42),
                    )
                ]
            ),
            "42",
        ),
        (
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
            ),
            "answer",
        ),
    ],
)
async def test_deepseek_rejects_malformed_choice_shapes(
    malformed_response: object, sensitive_text: str
) -> None:
    completions = RecordingCompletions(malformed_response)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete([ChatMessage(role="user", content="question")])

    assert len(completions.calls) == 1
    assert sensitive_text not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish_reason", ["length", "content_filter", "insufficient_system_resource"]
)
async def test_deepseek_terminal_finish_reasons_fail_closed(
    finish_reason: str,
) -> None:
    completions = RecordingCompletions(
        chat_response(text="partial output", finish_reason=finish_reason)
    )
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete([ChatMessage(role="user", content="question")])

    assert len(completions.calls) == 1
    assert "partial output" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (
            APITimeoutError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
            ProviderTimeoutError,
        ),
        (
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            APIConnectionError(request=httpx.Request("POST", DEEPSEEK_BASE_URL)),
            ProviderResponseError,
        ),
        (
            APIStatusError(
                "bad",
                response=httpx.Response(
                    401, request=httpx.Request("POST", DEEPSEEK_BASE_URL)
                ),
                body=None,
            ),
            ProviderResponseError,
        ),
    ],
)
async def test_deepseek_plain_translates_sdk_errors(raised, expected) -> None:
    completions = RecordingCompletions(raised)
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )
    async with tracker.session_span("session-1", "question"):
        with pytest.raises(expected):
            await provider.complete([ChatMessage(role="user", content="question")])


@pytest.mark.asyncio
async def test_deepseek_plain_translates_generic_openai_errors() -> None:
    completions = RecordingCompletions(OpenAIError("invalid"))
    tracker = local_tracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="request failed"):
            await provider.complete([ChatMessage(role="user", content="question")])


@pytest.mark.asyncio
async def test_deepseek_plain_observability_is_safe_and_capability_driven() -> None:
    completions = RecordingCompletions(
        chat_response(
            text="provider answer",
            prompt_tokens=8,
            completion_tokens=3,
            reasoning_content="hidden chain of thought",
        )
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [
                ChatMessage(role="developer", content="policy"),
                ChatMessage(role="user", content="question"),
            ],
            agent_name="planner",
        )

    assert result.text == "provider answer"
    metric = next(m for m in tracker.metrics if isinstance(m, TokenUsageMetric))
    assert metric.model == "deepseek-v4-flash"
    assert metric.input_tokens == 8
    assert metric.output_tokens == 3
    assert metric.total_tokens == 11
    assert metric.success is True

    assert tracker.llm_inputs == [
        {
            "provider": "deepseek",
            "thinking_mode": "enabled",
            "requested_reasoning_effort": "high",
            "effective_reasoning_effort": "high",
            "agent_name": "planner",
            "operation": "chat",
            "message_count": 2,
        }
    ]
    serialized = json.dumps(
        {
            "llm_inputs": tracker.llm_inputs,
            "events": [event.model_dump(mode="json") for event in tracker.events],
            "errors": [error.model_dump(mode="json") for error in tracker.errors],
            "metrics": [
                metric.model_dump(mode="json") for metric in tracker.metrics
            ],
        },
        sort_keys=True,
    )
    for sensitive in (
        "deepseek-secret",
        "policy",
        "question",
        "provider answer",
        "hidden chain of thought",
    ):
        assert sensitive not in serialized


@pytest.mark.asyncio
async def test_deepseek_plain_failure_records_typed_metric_without_leak() -> None:
    completions = RecordingCompletions(
        SimpleNamespace(
            id="deepseek-response",
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content="   ", reasoning_content="hidden chain of thought"
                    ),
                )
            ],
        )
    )
    tracker = CapturingTracker()
    provider = DeepSeekChatProvider(
        deepseek_config(), tracker, client=FakeDeepSeekClient(completions)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError) as caught:
            await provider.complete([ChatMessage(role="user", content="question")])

    metric = next(m for m in tracker.metrics if isinstance(m, TokenUsageMetric))
    assert metric.success is False
    assert metric.error_type == "ProviderResponseError"
    assert metric.model == "deepseek-v4-flash"
    serialized = json.dumps(
        {
            "llm_inputs": tracker.llm_inputs,
            "events": [event.model_dump(mode="json") for event in tracker.events],
            "errors": [error.model_dump(mode="json") for error in tracker.errors],
            "metrics": [
                metric.model_dump(mode="json") for metric in tracker.metrics
            ],
        },
        sort_keys=True,
    )
    assert "hidden chain of thought" not in serialized
    assert "question" not in serialized
    assert str(caught.value) == "DeepSeek response contained malformed content"
