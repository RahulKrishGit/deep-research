"""OpenAI embedding provider.

Long-term memory depends only on the ``embed_query``/``embed_documents``
protocol, so this module is the single place that knows about the OpenAI
client. The OpenAI package is imported lazily so importing the project does
not require it at collection time.
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
        request: dict[str, Any] = {"model": self.model, "input": payload}
        if self._dimensions is not None:
            request["dimensions"] = self._dimensions
        response = self._get_client().embeddings.create(**request)
        items = sorted(response.data, key=lambda item: item.index)
        if len(items) != len(payload):
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
