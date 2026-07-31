"""Tests for the OpenAI embedding provider used by long-term memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)


@dataclass
class FakeItem:
    index: int
    embedding: list[float]


@dataclass
class FakeResponse:
    data: list[FakeItem]


class FakeEmbeddingsEndpoint:
    def __init__(self, owner: "FakeOpenAIClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> FakeResponse:
        self._owner.requests.append(kwargs)
        if self._owner.error is not None:
            raise self._owner.error
        inputs = kwargs["input"]
        return FakeResponse(
            data=[
                FakeItem(index=index, embedding=[float(index), 1.0])
                for index in reversed(range(len(inputs)))
            ]
        )


class FakeOpenAIClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.error = error
        self.embeddings = FakeEmbeddingsEndpoint(self)


def test_embed_documents_sends_one_batched_request() -> None:
    client = FakeOpenAIClient()
    provider = OpenAIEmbeddingProvider(client=client)

    vectors = provider.embed_documents(["first", "second"])

    assert len(client.requests) == 1
    assert client.requests[0] == {
        "model": DEFAULT_EMBEDDING_MODEL,
        "input": ["first", "second"],
    }
    assert vectors == [[0.0, 1.0], [1.0, 1.0]]


def test_embed_documents_restores_the_requested_order() -> None:
    provider = OpenAIEmbeddingProvider(client=FakeOpenAIClient())

    vectors = provider.embed_documents(["a", "b", "c"])

    assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]


def test_embed_query_returns_a_single_vector() -> None:
    provider = OpenAIEmbeddingProvider(client=FakeOpenAIClient())

    assert provider.embed_query("question") == [0.0, 1.0]


def test_embed_documents_short_circuits_on_an_empty_batch() -> None:
    client = FakeOpenAIClient()
    provider = OpenAIEmbeddingProvider(client=client)

    assert provider.embed_documents([]) == []
    assert client.requests == []


def test_explicit_dimensions_are_forwarded_and_reported() -> None:
    client = FakeOpenAIClient()
    provider = OpenAIEmbeddingProvider(client=client, dimensions=256)

    provider.embed_documents(["text"])

    assert client.requests[0]["dimensions"] == 256
    assert provider.dimension == 256


def test_dimension_falls_back_to_the_known_model_table() -> None:
    assert OpenAIEmbeddingProvider().dimension == 1536
    assert (
        OpenAIEmbeddingProvider(model="text-embedding-3-large").dimension == 3072
    )
    assert OpenAIEmbeddingProvider(model="future-model").dimension is None


def test_blank_inputs_are_rejected_before_a_request_is_made() -> None:
    client = FakeOpenAIClient()
    provider = OpenAIEmbeddingProvider(client=client)

    with pytest.raises(ValueError, match="embedding input must not be blank"):
        provider.embed_documents(["ok", "  "])
    assert client.requests == []


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"model": "  "}, "model must not be blank"),
        ({"dimensions": 0}, "dimensions must be positive"),
    ],
)
def test_invalid_construction_is_rejected(
    kwargs: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        OpenAIEmbeddingProvider(**kwargs)


def test_provider_errors_propagate_to_the_memory_layer() -> None:
    provider = OpenAIEmbeddingProvider(
        client=FakeOpenAIClient(error=RuntimeError("rate limited"))
    )

    with pytest.raises(RuntimeError, match="rate limited"):
        provider.embed_query("question")


def test_short_response_is_rejected() -> None:
    client = FakeOpenAIClient()
    client.embeddings.create = lambda **kwargs: FakeResponse(
        data=[FakeItem(index=0, embedding=[1.0])]
    )
    provider = OpenAIEmbeddingProvider(client=client)

    with pytest.raises(ValueError, match="unexpected number of embeddings"):
        provider.embed_documents(["a", "b"])
