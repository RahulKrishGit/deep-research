"""ChromaDB-backed semantic memory for cross-session research recall.

Every operation is guarded: a backend or embedding failure records a
recoverable ``ResearchError`` and returns an empty result so agents can
continue with short-term state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from deep_research.memory.entries import (
    MemoryEntry,
    MemoryEntryType,
    MemoryQueryResult,
    SourceReputation,
    source_reputation_entry_id,
)
from deep_research.memory.errors import MemoryErrorLog, MemoryInitializationError
from deep_research.memory.instrumentation import memory_operation
from deep_research.observability import Tracker
from deep_research.utils.config import LongTermMemoryConfig
from deep_research.utils.types import ResearchError

DEFAULT_TOP_K = 5
_QUERY_INCLUDE = ["documents", "metadatas", "distances"]
_GET_INCLUDE = ["documents", "metadatas"]


class EmbeddingProvider(Protocol):
    def embed_query(self, text: str) -> list[float]:
        """Return one embedding vector for a search string."""
        raise NotImplementedError

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per stored document."""
        raise NotImplementedError


class VectorCollection(Protocol):
    def upsert(
        self,
        *,
        ids: Sequence[str],
        documents: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        """Insert or replace records by id."""
        raise NotImplementedError

    def query(
        self,
        *,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int,
        where: Mapping[str, Any] | None = None,
        include: Sequence[str] | None = None,
    ) -> Mapping[str, Any]:
        """Return the nearest records for each query vector."""
        raise NotImplementedError

    def get(
        self,
        *,
        ids: Sequence[str],
        include: Sequence[str] | None = None,
    ) -> Mapping[str, Any]:
        """Return records by exact id."""
        raise NotImplementedError

    def count(self) -> int:
        """Return the number of stored records."""
        raise NotImplementedError


def _build_where(
    *,
    entry_type: MemoryEntryType | None,
    where: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Translate filters into the backend's ``$eq``/``$and`` clause form."""
    clauses: list[dict[str, Any]] = []
    if entry_type is not None:
        clauses.append({"entry_type": {"$eq": entry_type}})
    for key, value in (where or {}).items():
        clauses.append(
            {key: dict(value)} if isinstance(value, Mapping) else {key: {"$eq": value}}
        )
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _first_row(value: Any) -> list[Any]:
    if not value:
        return []
    first = value[0]
    return [] if first is None else list(first)


def _parse_query_response(raw: Mapping[str, Any]) -> list[MemoryQueryResult]:
    ids = _first_row(raw.get("ids"))
    documents = _first_row(raw.get("documents"))
    metadatas = _first_row(raw.get("metadatas"))
    distances = _first_row(raw.get("distances"))
    results: list[MemoryQueryResult] = []
    for index, entry_id in enumerate(ids):
        if index >= len(documents) or index >= len(metadatas):
            raise ValueError("query response is missing documents or metadata")
        distance = distances[index] if index < len(distances) else None
        results.append(
            MemoryQueryResult(
                entry=MemoryEntry.from_storage(
                    entry_id=entry_id,
                    document=documents[index],
                    metadata=dict(metadatas[index]),
                ),
                distance=None if distance is None else max(0.0, float(distance)),
            )
        )
    return results


def _parse_get_response(raw: Mapping[str, Any]) -> list[MemoryEntry]:
    ids = list(raw.get("ids") or [])
    documents = list(raw.get("documents") or [])
    metadatas = list(raw.get("metadatas") or [])
    entries: list[MemoryEntry] = []
    for index, entry_id in enumerate(ids):
        if index >= len(documents) or index >= len(metadatas):
            raise ValueError("get response is missing documents or metadata")
        entries.append(
            MemoryEntry.from_storage(
                entry_id=entry_id,
                document=documents[index],
                metadata=dict(metadatas[index]),
            )
        )
    return entries


def build_chroma_collection(config: LongTermMemoryConfig) -> VectorCollection:
    """Open the persistent ChromaDB collection backing long-term memory.

    ``chromadb`` is imported lazily so that importing ``deep_research.memory``
    stays cheap and does not require the backend. Any failure here is a startup
    failure and is deliberately not recoverable.
    """
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as error:
        raise MemoryInitializationError(
            "chromadb is required for long-term memory; "
            'install the project with pip install -e ".[dev]"'
        ) from error

    directory = Path(config.persist_directory) / "chroma"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(directory),
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        return client.get_or_create_collection(
            name=config.collection_name,
            embedding_function=None,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as error:
        raise MemoryInitializationError(
            f"cannot open ChromaDB collection {config.collection_name!r} at "
            f"{directory}: {type(error).__name__}"
        ) from error


class LongTermMemory:
    """Semantic memory over verified findings, reputations, and summaries."""

    def __init__(
        self,
        *,
        collection: VectorCollection,
        embeddings: EmbeddingProvider,
        tracker: Tracker | None = None,
    ) -> None:
        self._collection = collection
        self._embeddings = embeddings
        self._tracker = tracker
        self._error_log = MemoryErrorLog("long_term_memory")

    @classmethod
    def from_config(
        cls,
        config: LongTermMemoryConfig,
        *,
        embeddings: EmbeddingProvider,
        tracker: Tracker | None = None,
    ) -> "LongTermMemory":
        """Open long-term memory against the configured ChromaDB directory."""
        return cls(
            collection=build_chroma_collection(config),
            embeddings=embeddings,
            tracker=tracker,
        )

    @property
    def errors(self) -> Sequence[ResearchError]:
        return self._error_log.errors

    def drain_errors(self) -> list[ResearchError]:
        return self._error_log.drain()

    async def save(self, entry: MemoryEntry) -> bool:
        """Store one entry. Returns False when memory is unavailable."""
        return await self.save_many([entry]) == 1

    async def save_many(self, entries: Sequence[MemoryEntry]) -> int:
        """Store many entries. Returns how many were written (0 on failure)."""
        batch = tuple(entries)
        if not batch:
            return 0
        entry_types = {entry.entry_type for entry in batch}
        entry_type = next(iter(entry_types)) if len(entry_types) == 1 else None
        try:
            async with memory_operation(
                self._tracker,
                "save",
                memory_layer="long_term",
                entry_type=entry_type,
            ) as span:
                documents = [entry.content for entry in batch]
                vectors = await asyncio.to_thread(
                    self._embeddings.embed_documents, documents
                )
                if len(vectors) != len(batch):
                    raise ValueError(
                        "embedding provider returned the wrong number of vectors"
                    )
                await asyncio.to_thread(
                    self._collection.upsert,
                    ids=[entry.entry_id for entry in batch],
                    documents=documents,
                    embeddings=[list(vector) for vector in vectors],
                    metadatas=[entry.to_metadata() for entry in batch],
                )
                span.set_result_count(len(batch))
        except Exception as error:
            self._record_unavailable(
                error, operation="save", details={"entry_count": len(batch)}
            )
            return 0
        return len(batch)

    async def query(
        self,
        text: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        entry_type: MemoryEntryType | None = None,
        where: Mapping[str, Any] | None = None,
    ) -> list[MemoryQueryResult]:
        """Semantic search with optional metadata filters.

        Backend and embedding failures return ``[]``; invalid arguments
        (``top_k < 1``, blank text) raise ``ValueError``.
        """
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if not text.strip():
            raise ValueError("query text must not be blank")
        filters = _build_where(entry_type=entry_type, where=where)
        try:
            async with memory_operation(
                self._tracker,
                "query",
                memory_layer="long_term",
                entry_type=entry_type,
                top_k=top_k,
            ) as span:
                vector = await asyncio.to_thread(self._embeddings.embed_query, text)
                raw = await asyncio.to_thread(
                    self._collection.query,
                    query_embeddings=[list(vector)],
                    n_results=top_k,
                    where=filters,
                    include=_QUERY_INCLUDE,
                )
                results = _parse_query_response(raw)
                span.set_result_count(len(results))
        except Exception as error:
            self._record_unavailable(
                error, operation="query", details={"top_k": top_k}
            )
            return []
        return results

    async def get_source_reputation(self, url: str) -> SourceReputation | None:
        """Return the stored reputation for one source, if any."""
        entry_id = source_reputation_entry_id(url)
        try:
            async with memory_operation(
                self._tracker,
                "get_source_reputation",
                memory_layer="long_term",
                entry_type="source_reputation",
            ) as span:
                raw = await asyncio.to_thread(
                    self._collection.get, ids=[entry_id], include=_GET_INCLUDE
                )
                entries = _parse_get_response(raw)
                span.set_result_count(len(entries))
        except Exception as error:
            self._record_unavailable(
                error, operation="get_source_reputation", details={}
            )
            return None
        if not entries:
            return None
        return SourceReputation.from_entry(entries[0])

    async def update_source_reputation(
        self,
        *,
        url: str,
        title: str,
        reputation_score: float,
        session_id: str,
        agent_id: str,
        notes: str = "",
    ) -> SourceReputation | None:
        """Blend a new judgement into the running reputation and persist it.

        Returns ``None`` both when the write fails and when the prior read
        fails: a failed read means we cannot tell whether a record already
        exists, so falling through to "create new" would silently overwrite
        accumulated history with a single fresh observation. In that case we
        leave the existing record untouched and surface the recorded error.
        """
        source_reputation_entry_id(url)  # raise on a blank url before opening a span
        try:
            async with memory_operation(
                self._tracker,
                "update_source_reputation",
                memory_layer="long_term",
                entry_type="source_reputation",
            ) as span:
                errors_before = len(self._error_log.errors)
                existing = await self.get_source_reputation(url)
                read_failed = (
                    existing is None and len(self._error_log.errors) > errors_before
                )
                if read_failed:
                    span.set_result_count(0)
                    return None
                normalized_score = min(1.0, max(0.0, reputation_score))
                clean_title = (title or "").strip()
                if existing is None:
                    record = SourceReputation(
                        url=url,
                        title=clean_title or url,
                        reputation_score=normalized_score,
                        observations=1,
                        notes=notes,
                    )
                else:
                    observations = existing.observations + 1
                    blended = (
                        existing.reputation_score * existing.observations
                        + normalized_score
                    ) / observations
                    record = SourceReputation(
                        url=existing.url,
                        title=clean_title or existing.title,
                        reputation_score=min(1.0, max(0.0, blended)),
                        observations=observations,
                        notes=notes or existing.notes,
                    )
                saved = await self.save(
                    record.to_entry(session_id=session_id, agent_id=agent_id)
                )
                span.set_result_count(1 if saved else 0)
        except Exception as error:
            self._record_unavailable(
                error, operation="update_source_reputation", details={}
            )
            return None
        return record if saved else None

    def _record_unavailable(
        self,
        error: BaseException,
        *,
        operation: str,
        details: Mapping[str, Any],
    ) -> None:
        self._error_log.record(
            error_type="long_term_memory_unavailable",
            message=(
                "Long-term memory is unavailable; "
                "agents continue with short-term state."
            ),
            error=error,
            details={"operation": operation, **dict(details)},
        )
