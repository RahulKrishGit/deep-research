"""Unit tests for the project-owned OpenAI provider boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    ContentFilterFinishReasonError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from deep_research.observability import (
    LangSmithRuntimeConfig,
    TokenUsageMetric,
    Tracker,
)
from deep_research.providers.openai_provider import (
    ChatMessage,
    OpenAIChatProvider,
    OpenAIEmbeddingProvider,
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


def embedding_response(vectors: list[list[float]]) -> SimpleNamespace:
    return SimpleNamespace(
        id="embedding-response",
        data=[
            SimpleNamespace(index=index, embedding=vector)
            for index, vector in reversed(list(enumerate(vectors)))
        ],
        usage=SimpleNamespace(prompt_tokens=6, total_tokens=6),
    )


class RecordingEmbeddings:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


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


def outline_validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as exc_info:
        Outline.model_validate({"title": 3, "points": "invalid"})
    return exc_info.value


@pytest.mark.asyncio
async def test_complete_structured_repairs_pydantic_validation_error() -> None:
    repaired = Outline(title="Repaired", points=["Valid"])
    responses = RecordingResponses(
        outline_validation_error(), response(parsed=repaired)
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


@pytest.mark.asyncio
async def test_complete_structured_raises_after_two_pydantic_validation_errors() -> (
    None
):
    responses = RecordingResponses(
        outline_validation_error(), outline_validation_error()
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


@pytest.mark.asyncio
async def test_embed_query_returns_vector_and_reports_dimension() -> None:
    embeddings = RecordingEmbeddings(embedding_response([[0.1, 0.2, 0.3]]))
    tracker = local_tracker()
    provider = OpenAIEmbeddingProvider(
        LLMConfig(embedding_model="text-embedding-3-small"),
        tracker,
        client=FakeOpenAIClient(embeddings=embeddings),
    )

    assert provider.dimension is None
    async with tracker.session_span("session-1", "question"):
        vector = await provider.embed_query("semantic query")

    assert vector == [0.1, 0.2, 0.3]
    assert provider.dimension == 3
    assert embeddings.calls == [
        {"model": "text-embedding-3-small", "input": ["semantic query"]}
    ]
    metric = next(m for m in tracker.metrics if isinstance(m, TokenUsageMetric))
    assert metric.input_tokens == 6
    assert metric.output_tokens == 0


@pytest.mark.asyncio
async def test_embed_texts_preserves_input_order() -> None:
    embeddings = RecordingEmbeddings(embedding_response([[1.0, 0.0], [0.0, 1.0]]))
    tracker = local_tracker()
    provider = OpenAIEmbeddingProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(embeddings=embeddings),
    )

    async with tracker.session_span("session-1", "question"):
        vectors = await provider.embed_texts(["first", "second"])

    assert vectors == [[1.0, 0.0], [0.0, 1.0]]
    assert provider.dimension == 2


@pytest.mark.asyncio
async def test_embed_texts_rejects_inconsistent_dimensions() -> None:
    embeddings = RecordingEmbeddings(embedding_response([[1.0, 0.0], [0.0, 1.0, 2.0]]))
    tracker = local_tracker()
    provider = OpenAIEmbeddingProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(embeddings=embeddings),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="dimension"):
            await provider.embed_texts(["first", "second"])


@pytest.mark.asyncio
async def test_embed_texts_rejects_empty_batches_without_api_call() -> None:
    embeddings = RecordingEmbeddings()
    provider = OpenAIEmbeddingProvider(
        LLMConfig(),
        local_tracker(),
        client=FakeOpenAIClient(embeddings=embeddings),
    )

    with pytest.raises(ValueError, match="at least one"):
        await provider.embed_texts([])

    assert embeddings.calls == []


@pytest.mark.asyncio
async def test_complete_translates_connection_errors() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(
                APIConnectionError(
                    request=httpx.Request("POST", "https://api.openai.com")
                )
            )
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="connection"):
            await provider.complete([ChatMessage(role="user", content="Answer")])


@pytest.mark.asyncio
async def test_complete_structured_translates_connection_errors() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(
                APIConnectionError(
                    request=httpx.Request("POST", "https://api.openai.com")
                )
            )
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="connection"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )


@pytest.mark.asyncio
async def test_embed_texts_translates_connection_errors() -> None:
    embeddings = RecordingEmbeddings(
        APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    )
    tracker = local_tracker()
    provider = OpenAIEmbeddingProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(embeddings=embeddings),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="connection"):
            await provider.embed_texts(["first"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_response",
    [
        SimpleNamespace(),
        SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])]),
        SimpleNamespace(data=[SimpleNamespace(index=0)]),
        SimpleNamespace(data=[SimpleNamespace(index=0, embedding=None)]),
        SimpleNamespace(data=[SimpleNamespace(index=0, embedding=["not-a-number"])]),
    ],
)
async def test_embed_texts_translates_malformed_response_shapes(
    malformed_response: object,
) -> None:
    embeddings = RecordingEmbeddings(malformed_response)
    tracker = local_tracker()
    provider = OpenAIEmbeddingProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(embeddings=embeddings),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError):
            await provider.embed_texts(["first"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("texts", "malformed_response"),
    [
        (
            ["first", "second"],
            SimpleNamespace(
                data=[
                    SimpleNamespace(index=0, embedding=[0.1]),
                    SimpleNamespace(index=0, embedding=[0.2]),
                ]
            ),
        ),
        (
            ["first"],
            SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[0.1])]),
        ),
        (
            ["first", "second"],
            SimpleNamespace(
                data=[
                    SimpleNamespace(index=0, embedding=[0.1]),
                    SimpleNamespace(index=1, embedding=[0.2]),
                    SimpleNamespace(index=1, embedding=[0.3]),
                ]
            ),
        ),
        (
            ["first"],
            SimpleNamespace(data=[SimpleNamespace(index="0", embedding=[0.1])]),
        ),
        (
            ["first"],
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[float("nan")])]),
        ),
    ],
)
async def test_embed_texts_rejects_invalid_indices_and_nonfinite_values(
    texts: list[str], malformed_response: object
) -> None:
    embeddings = RecordingEmbeddings(malformed_response)
    tracker = local_tracker()
    provider = OpenAIEmbeddingProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(embeddings=embeddings),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError):
            await provider.embed_texts(texts)


@pytest.mark.asyncio
async def test_complete_structured_translates_finish_reason_error() -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(ContentFilterFinishReasonError())
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="structured output"):
            await provider.complete_structured(
                [ChatMessage(role="user", content="Create an outline")], Outline
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("output_text", [None, 42])
async def test_complete_rejects_non_string_output_text(output_text: object) -> None:
    tracker = local_tracker()
    provider = OpenAIChatProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(
            responses=RecordingResponses(response(text=output_text))
        ),
    )

    async with tracker.session_span("session-1", "question"):
        with pytest.raises(ProviderResponseError, match="text output"):
            await provider.complete([ChatMessage(role="user", content="Answer")])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_response",
    [
        SimpleNamespace(
            output_text="Answer", usage=SimpleNamespace(input_tokens="bad")
        ),
        SimpleNamespace(
            output_text="Answer",
            output_parsed=Outline(title="Answer", points=[]),
            usage=SimpleNamespace(input_tokens=1, output_tokens="bad"),
        ),
        SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.1])],
            usage=SimpleNamespace(prompt_tokens="bad"),
        ),
    ],
)
async def test_provider_methods_reject_malformed_usage(
    malformed_response: object,
) -> None:
    tracker = local_tracker()
    chat = OpenAIChatProvider(
        LLMConfig(),
        tracker,
        client=FakeOpenAIClient(responses=RecordingResponses(malformed_response)),
    )
    embeddings = RecordingEmbeddings(malformed_response)
    embedding_provider = OpenAIEmbeddingProvider(
        LLMConfig(), tracker, client=FakeOpenAIClient(embeddings=embeddings)
    )

    async with tracker.session_span("session-1", "question"):
        if hasattr(malformed_response, "data"):
            with pytest.raises(ProviderResponseError, match="usage"):
                await embedding_provider.embed_texts(["text"])
        elif hasattr(malformed_response, "output_parsed"):
            with pytest.raises(ProviderResponseError, match="usage"):
                await chat.complete_structured(
                    [ChatMessage(role="user", content="Create an outline")], Outline
                )
        else:
            with pytest.raises(ProviderResponseError, match="usage"):
                await chat.complete([ChatMessage(role="user", content="Answer")])
