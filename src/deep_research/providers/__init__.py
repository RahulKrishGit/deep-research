"""Model and embedding providers. OpenAI only in the first build."""

from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "OpenAIEmbeddingProvider",
]
