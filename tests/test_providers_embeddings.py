"""Tests for the OpenAI embedding provider used by long-term memory."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from dataclasses import dataclass
from typing import Any

import pytest

from deep_research.providers import embeddings as embeddings_module
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


def test_embed_documents_chunks_batches_larger_than_the_request_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch larger than the per-request cap is split across requests.

    Mirrors the existing reversed-order fake in ``FakeEmbeddingsEndpoint``
    (each request's response comes back index-reversed) to prove that
    order-restoration across chunk boundaries isn't accidental: if chunk
    results were concatenated without per-chunk sorting, or if the wrong
    slice of ``texts`` were sent to the wrong request, the final vector
    order would be wrong.
    """
    monkeypatch.setattr(embeddings_module, "_MAX_INPUTS_PER_REQUEST", 2)
    client = FakeOpenAIClient()
    provider = OpenAIEmbeddingProvider(client=client)
    texts = ["a", "b", "c", "d", "e"]

    vectors = provider.embed_documents(texts)

    assert len(client.requests) == 3
    assert [request["input"] for request in client.requests] == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]
    # Each fake response reverses indices *within its own request*, so the
    # per-text vector cycles per chunk rather than climbing globally -- this
    # is what makes the assertion below prove real order-restoration across
    # chunk boundaries, not an accident of a monotonically increasing fake.
    assert vectors == [
        [0.0, 1.0],
        [1.0, 1.0],
        [0.0, 1.0],
        [1.0, 1.0],
        [0.0, 1.0],
    ]


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


def test_the_openai_package_is_not_imported_at_module_import_time() -> None:
    """Regression guard for the lazy-import constraint.

    ``openai`` is a real installed dependency in this environment, so a
    plain ``import deep_research.providers`` cannot tell a lazy
    ``from openai import OpenAI`` (inside ``_get_client``) apart from an
    eager one hoisted to module scope — both would pass identically. This
    test runs in a fresh subprocess and poisons ``sys.modules["openai"]``
    before importing, which makes *any* ``import openai`` statement raise
    ``ImportError`` immediately. If the lazy import is ever hoisted to
    module level, this subprocess exits non-zero and the test fails.
    """
    script = textwrap.dedent(
        """
        import sys

        sys.modules["openai"] = None  # any `import openai` now raises ImportError

        import deep_research.memory  # noqa: F401
        import deep_research.providers  # noqa: F401
        from deep_research.providers.embeddings import OpenAIEmbeddingProvider

        assert sys.modules["openai"] is None, "openai must not be imported yet"

        class FakeEmbeddings:
            def create(self, **kwargs):
                raise AssertionError("network call should not happen in this test")

        class FakeClient:
            embeddings = FakeEmbeddings()

        # Constructing with an injected client must not touch openai either.
        OpenAIEmbeddingProvider(client=FakeClient())
        assert sys.modules["openai"] is None, "openai must still not be imported"
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


class FakeEmbeddingFunction:
    """Stands in for chromadb's DefaultEmbeddingFunction. Never downloads."""

    def __init__(self, vectors=None) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def __call__(self, input):  # noqa: A002 - chromadb's parameter name
        payload = list(input)
        self.calls.append(payload)
        if self.vectors is not None:
            return self.vectors
        return [[0.5, 0.25] for _ in payload]


def test_local_embedding_provider_reports_the_models_fixed_dimension() -> None:
    from deep_research.providers import (
        LOCAL_EMBEDDING_DIMENSION,
        LocalEmbeddingProvider,
    )

    assert LOCAL_EMBEDDING_DIMENSION == 384
    assert LocalEmbeddingProvider().dimension == 384


def test_local_embedding_provider_embeds_documents_through_the_injected_function() -> (
    None
):
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction()
    provider = LocalEmbeddingProvider(embedding_function=function)

    assert provider.embed_documents(["alpha", "beta"]) == [
        [0.5, 0.25],
        [0.5, 0.25],
    ]
    assert function.calls == [["alpha", "beta"]]


def test_local_embedding_provider_embeds_a_query_as_one_document() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction(vectors=[[1.0, 2.0]])
    provider = LocalEmbeddingProvider(embedding_function=function)

    assert provider.embed_query("alpha") == [1.0, 2.0]
    assert function.calls == [["alpha"]]


def test_local_embedding_provider_coerces_numpy_style_rows_to_floats() -> None:
    """chromadb returns numpy arrays; the memory layer requires plain floats."""
    import array

    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction(vectors=[array.array("f", [0.5, 0.25])])
    provider = LocalEmbeddingProvider(embedding_function=function)

    vectors = provider.embed_documents(["alpha"])

    assert vectors == [[0.5, 0.25]]
    assert all(type(value) is float for value in vectors[0])


def test_local_embedding_provider_returns_nothing_for_no_texts() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction()

    assert LocalEmbeddingProvider(embedding_function=function).embed_documents([]) == []
    assert function.calls == []


def test_local_embedding_provider_rejects_blank_input() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    provider = LocalEmbeddingProvider(embedding_function=FakeEmbeddingFunction())

    with pytest.raises(ValueError, match="must not be blank"):
        provider.embed_documents(["alpha", "   "])


def test_local_embedding_provider_rejects_a_short_result() -> None:
    from deep_research.providers import LocalEmbeddingProvider

    function = FakeEmbeddingFunction(vectors=[[0.1, 0.2]])
    provider = LocalEmbeddingProvider(embedding_function=function)

    with pytest.raises(ValueError, match="unexpected number of embeddings"):
        provider.embed_documents(["alpha", "beta"])


def test_local_embedding_provider_never_builds_the_real_model_when_injected(
    monkeypatch,
) -> None:
    """The default function is what would download an ONNX model; an
    injected function must make that path unreachable, which is what keeps
    this suite offline."""
    import chromadb.utils.embedding_functions as chroma_embedding_functions

    from deep_research.providers import LocalEmbeddingProvider

    def exploding(*args, **kwargs):
        raise AssertionError("offline tests must not build the local model")

    monkeypatch.setattr(
        chroma_embedding_functions, "DefaultEmbeddingFunction", exploding
    )
    provider = LocalEmbeddingProvider(embedding_function=FakeEmbeddingFunction())

    assert provider.embed_query("alpha") == [0.5, 0.25]
