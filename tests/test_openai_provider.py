"""Unit tests for the project-owned OpenAI provider boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import APIStatusError, APITimeoutError, RateLimitError
from pydantic import BaseModel

from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers.openai_provider import (
    ChatMessage,
    OpenAIChatProvider,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StructuredOutputError,
)
from deep_research.utils.config import LLMConfig


def response(
    *,
    text: str = "A concise answer.",
    parsed: object | None = None,
    input_tokens: int = 8,
    output_tokens: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="resp-1",
        output_text=text,
        output_parsed=parsed,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


class RecordingResponses:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.create_calls: list[dict[str, Any]] = []
        self.parse_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.create_calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def parse(self, **kwargs: Any) -> object:
        self.parse_calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeOpenAIClient:
    def __init__(
        self,
        *,
        responses: RecordingResponses | None = None,
        embeddings: object | None = None,
    ) -> None:
        self.responses = responses or RecordingResponses()
        self.embeddings = embeddings


def local_tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


@pytest.mark.asyncio
async def test_complete_parses_text_and_records_usage() -> None:
    responses = RecordingResponses(response())
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(model_overrides={"planner": "gpt-4o-mini"}),
        tracker,
        client=FakeOpenAIClient(responses=responses),
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete(
            [ChatMessage(role="user", content="Summarize this.")],
            agent_name="planner",
        )

    assert result.text == "A concise answer."
    assert result.model == "gpt-4o-mini"
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 3
    assert responses.create_calls == [
        {
            "model": "gpt-4o-mini",
            "input": [{"role": "user", "content": "Summarize this."}],
            "temperature": 0.7,
            "max_output_tokens": 4096,
        }
    ]
    token_metric = next(
        metric for metric in tracker.metrics if isinstance(metric, TokenUsageMetric)
    )
    assert token_metric.model == "gpt-4o-mini"
    assert token_metric.total_tokens == 11
    assert tracker.events[-1].metadata["success"] is True


def test_missing_api_key_fails_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        OpenAIChatProvider(LLMConfig(), local_tracker())


def test_explicit_empty_api_key_does_not_fall_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    with pytest.raises(ProviderConfigurationError, match="OPENAI_API_KEY"):
        OpenAIChatProvider(LLMConfig(), local_tracker(), api_key="")


class Outline(BaseModel):
    title: str
    points: list[str]


@pytest.mark.asyncio
async def test_complete_structured_returns_parsed_model() -> None:
    parsed = Outline(title="Answer", points=["One", "Two"])
    responses = RecordingResponses(response(parsed=parsed))
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")], Outline
        )

    assert result == parsed
    assert responses.parse_calls[0]["text_format"] is Outline
    assert responses.parse_calls[0]["model"] == "gpt-4o"
    assert len([m for m in tracker.metrics if isinstance(m, TokenUsageMetric)]) == 1


@pytest.mark.asyncio
async def test_complete_structured_repairs_once_then_succeeds() -> None:
    repaired = Outline(title="Repaired", points=["Valid"])
    responses = RecordingResponses(
        response(text='{"title": 3}', parsed=None, input_tokens=5, output_tokens=2),
        response(parsed=repaired, input_tokens=7, output_tokens=3),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        result = await provider.complete_structured(
            [ChatMessage(role="user", content="Create an outline")], Outline
        )

    assert result == repaired
    assert len(responses.parse_calls) == 2
    repair_input = responses.parse_calls[1]["input"]
    assert repair_input[-1]["role"] == "developer"
    assert "failed Outline validation" in repair_input[-1]["content"]
    token_metrics = [m for m in tracker.metrics if isinstance(m, TokenUsageMetric)]
    assert [metric.total_tokens for metric in token_metrics] == [7, 10]
    assert [metric.success for metric in token_metrics] == [False, True]


@pytest.mark.asyncio
async def test_complete_structured_raises_after_one_failed_repair() -> None:
    responses = RecordingResponses(
        response(text="invalid", parsed=None),
        response(text="still invalid", parsed=None),
    )
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(), tracker, client=FakeOpenAIClient(responses=responses)
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(StructuredOutputError, match="Outline"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )

    assert len(responses.parse_calls) == 2
    token_metrics = [m for m in tracker.metrics if isinstance(m, TokenUsageMetric)]
    assert [metric.success for metric in token_metrics] == [False, False]


@pytest.mark.asyncio
async def test_empty_text_response_is_a_typed_provider_error() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(responses=RecordingResponses(response(text="   "))),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="text output"):
            await provider.complete([ChatMessage(role="user", content="Answer")])

    metric = next(m for m in tracker.metrics if isinstance(m, TokenUsageMetric))
    assert metric.success is False
    assert metric.error_type == "ProviderResponseError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sdk_error", "expected_type"),
    [
        (
            APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
            ProviderTimeoutError,
        ),
        (
            RateLimitError(
                "limited",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://api.openai.com")
                ),
                body=None,
            ),
            ProviderRateLimitError,
        ),
        (
            APIStatusError(
                "failed",
                response=httpx.Response(
                    500, request=httpx.Request("POST", "https://api.openai.com")
                ),
                body=None,
            ),
            ProviderResponseError,
        ),
    ],
)
async def test_complete_translates_sdk_errors(
    sdk_error: Exception, expected_type: type[Exception]
) -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(responses=RecordingResponses(sdk_error)),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(expected_type):
            await provider.complete([ChatMessage(role="user", content="Answer")])
