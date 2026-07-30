"""Unit tests for the project-owned OpenAI provider boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers.openai_provider import (
    ChatMessage,
    OpenAIChatProvider,
    ProviderConfigurationError,
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
