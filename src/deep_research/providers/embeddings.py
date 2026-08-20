"""OpenAI and local embedding providers.

Long-term memory depends only on the ``embed_query``/``embed_documents``
protocol that both ``OpenAIEmbeddingProvider`` and ``LocalEmbeddingProvider``
implement, so this module is the single place that knows about either
backend. Both third-party clients (the OpenAI client, chromadb's default
embedding function) are imported lazily so importing the project does not
require them at collection time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_KNOWN_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
# OpenAI caps the number of inputs accepted in a single embeddings request at
# 2048. This constant chunks requests by *count* only; it does not address
# OpenAI's per-request token cap or per-input token cap -- a single oversized
# document can still fail a request, and that's a known, separate limitation
# not addressed here.
_MAX_INPUTS_PER_REQUEST = 2048


class OpenAIEmbeddingProvider:
    """Embed research text with an OpenAI embedding model."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int | None = None,
        client: Any | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be blank")
        if dimensions is not None and dimensions < 1:
            raise ValueError("dimensions must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.model = model.strip()
        self._dimensions = dimensions
        self._client = client
        self._api_key = api_key
        self._timeout = timeout

    @property
    def dimension(self) -> int | None:
        """Return the vector width when it is known without calling the API."""
        if self._dimensions is not None:
            return self._dimensions
        return _KNOWN_DIMENSIONS.get(self.model)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        payload = list(texts)
        if not payload:
            return []
        if any(not text.strip() for text in payload):
            raise ValueError("embedding input must not be blank")
        vectors: list[list[float]] = []
        for start in range(0, len(payload), _MAX_INPUTS_PER_REQUEST):
            chunk = payload[start : start + _MAX_INPUTS_PER_REQUEST]
            vectors.extend(self._embed_chunk(chunk))
        return vectors

    def _embed_chunk(self, chunk: Sequence[str]) -> list[list[float]]:
        request: dict[str, Any] = {"model": self.model, "input": list(chunk)}
        if self._dimensions is not None:
            request["dimensions"] = self._dimensions
        response = self._get_client().embeddings.create(**request)
        items = sorted(response.data, key=lambda item: item.index)
        if len(items) != len(chunk) or [item.index for item in items] != list(
            range(len(chunk))
        ):
            raise ValueError("OpenAI returned an unexpected number of embeddings")
        return [list(item.embedding) for item in items]

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise RuntimeError(
                    "the openai package is required for embeddings; "
                    'install the project with pip install -e ".[dev]"'
                ) from error
            self._client = OpenAI(api_key=self._api_key, timeout=self._timeout)
        return self._client


LOCAL_EMBEDDING_PROVIDER = "local"
# chromadb's bundled all-MiniLM-L6-v2 ONNX model. Fixed here rather than
# looked up from a per-model-name table: there is exactly one local model.
LOCAL_EMBEDDING_DIMENSION = 384


class LocalEmbeddingProvider:
    """Embed research text with chromadb's bundled default ONNX model.

    Implements the same ``embed_query``/``embed_documents`` protocol as
    ``OpenAIEmbeddingProvider``, so ``LongTermMemory`` cannot tell the two
    apart. There is no API key and no per-call cost: the model is fetched
    once into chromadb's local cache and every embedding after that is
    computed in-process.

    ``embedding_function`` is injectable so tests never construct the real
    function, which is the object that would download the model.
    """

    def __init__(self, *, embedding_function: Any | None = None) -> None:
        self._embedding_function = embedding_function

    @property
    def dimension(self) -> int:
        """The model's fixed vector width, known without any call."""
        return LOCAL_EMBEDDING_DIMENSION

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        payload = list(texts)
        if not payload:
            return []
        if any(not text.strip() for text in payload):
            raise ValueError("embedding input must not be blank")
        vectors = self._get_embedding_function()(payload)
        rows = list(vectors)
        if len(rows) != len(payload):
            raise ValueError(
                "the local embedding model returned an unexpected number "
                "of embeddings"
            )
        # chromadb hands back numpy float32 rows; the Chroma collection and
        # every artifact downstream want plain Python floats.
        return [[float(value) for value in row] for row in rows]

    def _get_embedding_function(self) -> Any:
        if self._embedding_function is None:
            try:
                from chromadb.utils.embedding_functions import (
                    DefaultEmbeddingFunction,
                )
            except ImportError as error:
                raise RuntimeError(
                    "the chromadb package is required for local embeddings; "
                    'install the project with pip install -e ".[dev]"'
                ) from error
            self._embedding_function = DefaultEmbeddingFunction()
        return self._embedding_function
