"""In-memory test doubles for the long-term memory collaborators.

This module is deliberately not named ``test_*.py`` so pytest does not try to
collect it as a test module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from math import sqrt
from typing import Any


def _vector(text: str, dimension: int) -> list[float]:
    digest = sha256(text.encode("utf-8")).digest()
    return [digest[index % len(digest)] / 255.0 for index in range(dimension)]


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return sqrt(
        sum((a - b) ** 2 for a, b in zip(left, right, strict=True))
    )


def _matches(where: Mapping[str, Any] | None, metadata: Mapping[str, Any]) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_matches(clause, metadata) for clause in where["$and"])
    for key, condition in where.items():
        expected = condition["$eq"] if isinstance(condition, Mapping) else condition
        if metadata.get(key) != expected:
            return False
    return True


class FakeEmbeddings:
    """Deterministic, offline stand-in for an OpenAI embedding provider."""

    def __init__(self, dimension: int = 8) -> None:
        self.dimension = dimension
        self.calls: list[tuple[str, int]] = []
        self.fail = False

    def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise RuntimeError("embedding provider unavailable")
        self.calls.append(("query", 1))
        return _vector(text, self.dimension)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("embedding provider unavailable")
        items = list(texts)
        self.calls.append(("documents", len(items)))
        return [_vector(text, self.dimension) for text in items]


class FakeCollection:
    """Brute-force in-memory stand-in for a ChromaDB collection."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.last_where: Any = None
        self.fail_on: set[str] = set()

    def upsert(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        if "upsert" in self.fail_on:
            raise RuntimeError("vector store unavailable")
        for entry_id, document, embedding, metadata in zip(
            ids, documents, embeddings, metadatas, strict=True
        ):
            self.records[entry_id] = {
                "document": document,
                "embedding": list(embedding),
                "metadata": dict(metadata),
            }

    def get(
        self,
        *,
        ids: Sequence[str],
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if "get" in self.fail_on:
            raise RuntimeError("vector store unavailable")
        found = [
            (entry_id, self.records[entry_id])
            for entry_id in ids
            if entry_id in self.records
        ]
        return {
            "ids": [entry_id for entry_id, _ in found],
            "documents": [record["document"] for _, record in found],
            "metadatas": [record["metadata"] for _, record in found],
        }

    def query(
        self,
        *,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int,
        where: Mapping[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if "query" in self.fail_on:
            raise RuntimeError("vector store unavailable")
        self.last_where = where
        vector = list(query_embeddings[0])
        matches = [
            (entry_id, record)
            for entry_id, record in self.records.items()
            if _matches(where, record["metadata"])
        ]
        matches.sort(key=lambda item: _distance(vector, item[1]["embedding"]))
        matches = matches[:n_results]
        return {
            "ids": [[entry_id for entry_id, _ in matches]],
            "documents": [[record["document"] for _, record in matches]],
            "metadatas": [[record["metadata"] for _, record in matches]],
            "distances": [
                [_distance(vector, record["embedding"]) for _, record in matches]
            ],
        }

    def count(self) -> int:
        return len(self.records)
