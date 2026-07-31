# Memory Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three-layer agent memory system — a bounded in-session scratchpad, a ChromaDB-backed semantic long-term store with OpenAI embeddings, and a JSON-backed procedural strategy registry — so that later agents can save and recall research knowledge without any memory failure stopping a research run.

**Architecture:** All three layers live under `src/deep_research/memory/` and share one set of Pydantic record models (`entries.py`) and one recoverable-error collector (`errors.py`). `ScratchpadMemory` is synchronous and pure in-memory. `LongTermMemory` and `ProceduralMemory` are async, take injected collaborators (a vector collection protocol, an embedding provider protocol, a filesystem path), run blocking I/O through `asyncio.to_thread`, and never raise during research — they record a recoverable `ResearchError` and return an empty/false result. Observability reuses the existing `Tracker` span engine: a new `MemoryMetric` schema plus a `Tracker.memory_span(...)` method, wrapped by a small `memory_operation(...)` helper that degrades to a no-op when no tracker or no active session span is present.

**Tech Stack:** Python 3.11+, Pydantic 2, ChromaDB (`PersistentClient`), OpenAI Python SDK (embeddings), LangSmith SDK 0.10+ (through the existing `Tracker`), pytest, pytest-asyncio, Ruff

## Global Constraints

- Preserve `requires-python = ">=3.11"`.
- `ScratchpadMemory` is per-agent and per-session, bounded, and **never persisted**.
- `LongTermMemory` stores exactly these entry types: `finding`, `source_reputation`, `report_summary`, `failed_strategy`.
- ChromaDB is the only vector backend. No other external vector database.
- OpenAI is the only embedding provider.
- Long-term memory persists under `<memory.long_term.persist_directory>/chroma/`; procedural memory persists at `memory/strategies.json` by default.
- Memory failures are recoverable **except** startup initialization failure. Recoverable failures append a `ResearchError` and let the caller continue; startup failures raise `MemoryInitializationError`.
- If long-term memory is unavailable during research, callers continue with short-term state: `save*` returns `0`/`False`, `query` returns `[]`, and a `ResearchError` is recorded.
- Corrupt `strategies.json` is renamed to `strategies.json.corrupt-<UTC timestamp>.bak` and the registry restarts empty.
- Every long-term and procedural memory operation emits: operation name, memory layer, entry type (when known), query top-k (when applicable), result count, latency, and success/error type.
- No memory test may read or write the repository's runtime `memory/` or `output/` directories. Use `tmp_path`.
- No test may make a real OpenAI, ChromaDB-cloud, or LangSmith network call.
- Recorded error `details` carry `exception_type` only — never `str(exception)` — matching the existing tracker convention, so provider messages cannot leak secrets into `ResearchState`.
- Do not implement agents, tools (`SaveToMemoryTool`/`QueryMemoryTool`), the chat provider, or LangGraph wiring. Those belong to specs 05, 04, 07-11.

## Known Risks And Unknowns

Flagged explicitly rather than guessed at. Resolve each where noted.

1. **`chromadb` is not installed in this environment.** `pip list` shows `langsmith`, `openai`, `pydantic`, `pytest`, `pytest-asyncio` only. Task 7 adds the dependency and installs it. It is a heavy install (pulls `onnxruntime`, `numpy`, `grpcio`). Tasks 1-6 are deliberately ordered before it so an install problem blocks only the last adapter task.
2. **ChromaDB API drift.** This plan targets the `chromadb>=0.5,<2` client surface: `chromadb.PersistentClient(path=...)`, `client.get_or_create_collection(name=..., embedding_function=None, metadata=...)`, `collection.upsert(ids=, documents=, embeddings=, metadatas=)`, `collection.query(query_embeddings=, n_results=, where=, include=)`, `collection.get(ids=, include=)`. Task 7 Step 6 verifies the adapter against the version actually installed. If `embedding_function=None` is rejected by the installed version, pass a callable that raises instead (we always supply explicit vectors, so it must never be invoked); if `include=[...]` string members are rejected, use the version's `IncludeEnum` members. Record whatever adjustment was needed in the commit message.
3. **Two ChromaDB clients on one path.** `test_source_reputation_updates_persist_across_instances` (Task 7) opens `PersistentClient` twice for the same directory in one process. ChromaDB shares a system per (path, settings) pair, so identical `Settings(...)` reuse the same system; differing settings raise `ValueError: An instance of Chroma already exists for ... with different settings`. If that error appears, it means `build_chroma_collection` is not constructing `Settings` deterministically — fix the factory, do not change the test.
4. **Overlap with spec 04 (OpenAI Provider).** Spec 04 owns `OpenAIChatProvider` and `OpenAIEmbeddingProvider` in `src/deep_research/providers/openai_provider.py`, but spec 04 is not implemented yet and this spec lists "OpenAI embedding integration" in its own scope. Task 6 therefore creates `src/deep_research/providers/embeddings.py` with the class named **exactly** `OpenAIEmbeddingProvider` so the spec 04 plan can re-export or absorb it without a rename. Do not name it anything else.
5. **`MemoryError` is a Python builtin.** The base exception here is named `MemoryStackError`. Do not "correct" it to `MemoryError` — that would shadow the interpreter's allocation-failure exception.
6. **Scratchpad summarization hook is synchronous.** `Summarizer = Callable[[Sequence[ScratchpadEntry]], str]`. An LLM-backed summarizer is async, so a later agent task may need an async variant. Deliberately deferred (YAGNI): no agent exists yet, and a sync hook keeps `ScratchpadMemory` usable inside tight ReAct loops without an event loop.

## Design Trade-Offs

- **Sync scratchpad, async long-term/procedural.** The scratchpad does no I/O and is written on every ReAct step; making it async would force an `await` into every agent thought with no benefit. The other two layers do disk and network I/O and must integrate with the async `Tracker` span API. The inconsistency is intentional and documented in the README.
- **Scratchpad emits no observability spans.** One span per thought inside a ReAct loop would swamp traces with zero-latency records. Summarizer failures are still captured as `ResearchError`s. Long-term and procedural memory — the layers with real latency and real storage errors — carry the spec's required instrumentation.
- **`success_rate` and `average_iterations` are derived properties, not stored fields.** `StrategyRecord` stores `sessions`, `successes`, and `total_iterations`. Storing the derived rate too would let the file drift out of self-consistency, and Pydantic `computed_field` values break `extra="forbid"` round-tripping when the dumped JSON is re-validated on load.
- **Injected collaborators over inherited backends.** `LongTermMemory` takes a `VectorCollection` and an `EmbeddingProvider`; `ProceduralMemory` takes a `Path`. Every behavior except the thin ChromaDB adapter is testable with fakes and no heavy dependency, and no test can reach a production runtime directory.
- **Broad `except Exception` in long-term memory.** The spec requires research to survive any memory failure. `BaseException` (including `asyncio.CancelledError` and `KeyboardInterrupt`) is deliberately *not* caught, so cancellation and interrupts still propagate.
- **Exceptions escape the observability span, then get caught.** Each guarded operation puts `try:` *outside* `async with memory_operation(...)`. The span therefore records `success=False` with the real `error_type`, and the caller still receives a recoverable result. Swallowing inside the span would report false successes.

## File Structure

- Create `src/deep_research/memory/entries.py` — Pydantic record models (`MemoryEntry`, `MemoryQueryResult`, `SourceReputation`, `ScratchpadEntry`, `StrategyRecord`) and the deterministic source-reputation id helper.
- Create `src/deep_research/memory/errors.py` — `MemoryStackError`, `MemoryInitializationError`, and `MemoryErrorLog` (recoverable-error collection shared by all three layers).
- Create `src/deep_research/memory/scratchpad.py` — `ScratchpadMemory`.
- Create `src/deep_research/memory/instrumentation.py` — `memory_operation(...)`, the optional/no-op wrapper around `Tracker.memory_span`.
- Create `src/deep_research/memory/procedural.py` — `ProceduralMemory` and atomic JSON persistence.
- Create `src/deep_research/memory/long_term.py` — `EmbeddingProvider`/`VectorCollection` protocols, `LongTermMemory`, `build_chroma_collection`.
- Modify `src/deep_research/memory/__init__.py` — public exports (currently a stub docstring).
- Create `src/deep_research/providers/embeddings.py` — `OpenAIEmbeddingProvider`.
- Modify `src/deep_research/providers/__init__.py` — public exports (currently a stub docstring).
- Modify `src/deep_research/observability/metrics.py` — add `MemoryLayer` and `MemoryMetric`, extend `MetricRecord`.
- Modify `src/deep_research/observability/tracker.py` — add `"memory"` to `SpanKind`, add `SpanHandle.result_count`/`set_result_count`, add `Tracker.memory_span`.
- Modify `src/deep_research/observability/__init__.py` — export `MemoryLayer` and `MemoryMetric`.
- Modify `src/deep_research/utils/config.py` and `config.yaml` — add `memory.procedural.strategies_path` plus its environment override.
- Modify `pyproject.toml` — add `openai` (Task 6) and `chromadb` (Task 7) runtime dependencies.
- Modify `README.md` and `tests/test_imports.py` — document and pin the public surface.
- Create `tests/memory_fakes.py` — shared `FakeEmbeddings`/`FakeCollection` test doubles (not collected: it does not match `python_files = ["test_*.py"]`).
- Create `tests/test_memory_entries.py`, `tests/test_memory_scratchpad.py`, `tests/test_memory_instrumentation.py`, `tests/test_memory_procedural.py`, `tests/test_memory_long_term.py`, `tests/test_memory_chroma.py`, `tests/test_providers_embeddings.py`.
- Modify `tests/test_config.py`, `tests/test_observability_metrics.py`, `tests/test_observability_tracker.py`.

---

### Task 1: Memory Entry Models And Error Contracts

**Files:**
- Create: `src/deep_research/memory/entries.py`
- Create: `src/deep_research/memory/errors.py`
- Create: `tests/test_memory_entries.py`

**Interfaces:**
- Consumes: `deep_research.utils.types.ContractModel`, `AwareISOString`, `UnitScore`, `ResearchError`.
- Produces:
  - `MemoryEntryType = Literal["finding", "source_reputation", "report_summary", "failed_strategy"]`
  - `ScratchpadEntryKind = Literal["thought", "observation", "decision", "summary"]`
  - `MetadataValue = str | int | float | bool`
  - `MemoryEntry(entry_id, entry_type, content, session_id, agent_id, confidence, source_url, source_title, timestamp, attributes)` with `to_metadata() -> dict[str, MetadataValue]` and `MemoryEntry.from_storage(*, entry_id: str, document: str, metadata: Mapping[str, MetadataValue]) -> MemoryEntry`
  - `MemoryQueryResult(entry: MemoryEntry, distance: float | None)` with `relevance: float` property
  - `SourceReputation(url, title, reputation_score, observations, notes, last_updated)` with `to_entry(*, session_id: str, agent_id: str) -> MemoryEntry` and `SourceReputation.from_entry(entry: MemoryEntry) -> SourceReputation`
  - `ScratchpadEntry(agent_name, kind, content, timestamp, metadata)`
  - `StrategyRecord(topic_type, query_templates, trusted_source_patterns, sessions, successes, total_iterations, notes, updated_at)` with `success_rate: float` and `average_iterations: float` properties
  - `source_reputation_entry_id(url: str) -> str`
  - `MemoryStackError`, `MemoryInitializationError(MemoryStackError)`
  - `MemoryErrorLog(source: str)` with `errors: Sequence[ResearchError]`, `record(*, error_type, message, error=None, details=None) -> ResearchError`, `drain() -> list[ResearchError]`

- [ ] **Step 1: Write the failing model tests**

Create `tests/test_memory_entries.py`:

```python
"""Tests for shared memory record models and error contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.memory.entries import (
    MemoryEntry,
    MemoryQueryResult,
    SourceReputation,
    StrategyRecord,
    source_reputation_entry_id,
)
from deep_research.memory.errors import (
    MemoryErrorLog,
    MemoryInitializationError,
    MemoryStackError,
)


def _entry(**overrides: object) -> MemoryEntry:
    payload: dict[str, object] = {
        "entry_type": "finding",
        "content": "Quantum error correction improved in 2025.",
        "session_id": "session-1",
        "agent_id": "researcher",
        "confidence": 0.8,
        "source_url": "https://example.org/qec",
        "source_title": "QEC Review",
        "timestamp": "2026-07-25T10:00:00+00:00",
    }
    payload.update(overrides)
    return MemoryEntry.model_validate(payload)


def test_memory_entry_generates_a_unique_default_id() -> None:
    first = _entry()
    second = _entry()

    assert first.entry_id != second.entry_id
    assert len(first.entry_id) > 0


def test_memory_entry_round_trips_through_flat_storage_metadata() -> None:
    entry = _entry(entry_id="entry-1", attributes={"sub_topic": "hardware"})

    metadata = entry.to_metadata()
    assert metadata == {
        "entry_type": "finding",
        "session_id": "session-1",
        "agent_id": "researcher",
        "confidence": 0.8,
        "timestamp": "2026-07-25T10:00:00+00:00",
        "source_url": "https://example.org/qec",
        "source_title": "QEC Review",
        "sub_topic": "hardware",
    }
    assert all(isinstance(value, (str, int, float, bool)) for value in metadata.values())

    restored = MemoryEntry.from_storage(
        entry_id="entry-1",
        document=entry.content,
        metadata=metadata,
    )
    assert restored == entry


def test_memory_entry_rejects_attributes_that_shadow_reserved_metadata() -> None:
    with pytest.raises(ValidationError, match="reserved metadata keys"):
        _entry(attributes={"session_id": "other"})


def test_memory_entry_rejects_unknown_entry_types() -> None:
    with pytest.raises(ValidationError):
        _entry(entry_type="rumour")


def test_memory_query_result_derives_relevance_from_distance() -> None:
    assert MemoryQueryResult(entry=_entry(), distance=0.0).relevance == 1.0
    assert MemoryQueryResult(entry=_entry(), distance=1.0).relevance == 0.5
    assert MemoryQueryResult(entry=_entry(), distance=None).relevance == 1.0


def test_source_reputation_entry_id_is_deterministic_per_url() -> None:
    first = source_reputation_entry_id("https://example.org/a")
    second = source_reputation_entry_id("  https://example.org/a  ")
    other = source_reputation_entry_id("https://example.org/b")

    assert first == second
    assert first != other
    assert first.startswith("source_reputation:")


def test_source_reputation_round_trips_through_a_memory_entry() -> None:
    record = SourceReputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=0.75,
        observations=3,
        notes="Peer reviewed.",
        last_updated="2026-07-25T10:00:00+00:00",
    )

    entry = record.to_entry(session_id="session-1", agent_id="source_evaluator")

    assert entry.entry_id == source_reputation_entry_id("https://example.org/a")
    assert entry.entry_type == "source_reputation"
    assert entry.confidence == 0.75
    assert SourceReputation.from_entry(entry) == record


def test_source_reputation_rejects_entries_of_the_wrong_type() -> None:
    with pytest.raises(ValueError, match="source_reputation"):
        SourceReputation.from_entry(_entry())


def test_strategy_record_derives_rates_from_stored_counts() -> None:
    record = StrategyRecord(
        topic_type="technology",
        sessions=4,
        successes=3,
        total_iterations=10,
    )

    assert record.success_rate == 0.75
    assert record.average_iterations == 2.5


def test_empty_strategy_record_reports_zero_rates() -> None:
    record = StrategyRecord(topic_type="technology")

    assert record.success_rate == 0.0
    assert record.average_iterations == 0.0


def test_strategy_record_rejects_more_successes_than_sessions() -> None:
    with pytest.raises(ValidationError, match="successes cannot exceed sessions"):
        StrategyRecord(topic_type="technology", sessions=1, successes=2)


def test_strategy_record_json_round_trips_without_extra_keys() -> None:
    record = StrategyRecord(
        topic_type="technology",
        query_templates=["{topic} benchmark 2026"],
        sessions=1,
        successes=1,
        total_iterations=2,
    )

    assert StrategyRecord.model_validate(record.model_dump(mode="json")) == record


def test_memory_initialization_error_is_a_memory_stack_error() -> None:
    assert issubclass(MemoryInitializationError, MemoryStackError)
    assert not issubclass(MemoryStackError, MemoryError)


def test_error_log_records_recoverable_errors_without_leaking_messages() -> None:
    log = MemoryErrorLog("long_term_memory")

    recorded = log.record(
        error_type="long_term_memory_unavailable",
        message="Long-term memory write failed.",
        error=RuntimeError("token sk-secret rejected"),
        details={"operation": "save"},
    )

    assert recorded.source == "long_term_memory"
    assert recorded.recoverable is True
    assert recorded.details == {
        "operation": "save",
        "exception_type": "RuntimeError",
    }
    assert "sk-secret" not in recorded.model_dump_json()
    assert len(log.errors) == 1


def test_error_log_drain_returns_and_clears_recorded_errors() -> None:
    log = MemoryErrorLog("procedural_memory")
    log.record(error_type="procedural_memory_corrupt", message="Corrupt file.")

    drained = log.drain()

    assert len(drained) == 1
    assert log.errors == ()
    assert log.drain() == []
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_memory_entries.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.memory.entries'`.

- [ ] **Step 3: Write the error contracts**

Create `src/deep_research/memory/errors.py`:

```python
"""Memory subsystem exceptions and recoverable-error collection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from deep_research.utils.types import ResearchError


class MemoryStackError(Exception):
    """Base class for memory subsystem failures.

    Named ``MemoryStackError`` rather than ``MemoryError`` so it never shadows
    the built-in allocation-failure exception.
    """


class MemoryInitializationError(MemoryStackError):
    """Raised when a memory layer cannot start. Not recoverable."""


class MemoryErrorLog:
    """Collect recoverable memory failures as structured research errors."""

    def __init__(self, source: str) -> None:
        if not source.strip():
            raise ValueError("source must not be blank")
        self._source = source.strip()
        self._errors: list[ResearchError] = []

    @property
    def source(self) -> str:
        return self._source

    @property
    def errors(self) -> Sequence[ResearchError]:
        return tuple(self._errors)

    def record(
        self,
        *,
        error_type: str,
        message: str,
        error: BaseException | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> ResearchError:
        """Append one recoverable error and return it.

        Only the exception *type* is recorded. Exception text can carry API
        keys, URLs, and file paths, and these errors are copied into
        ``ResearchState.errors``.
        """
        payload: dict[str, JsonValue] = dict(details or {})
        if error is not None:
            payload["exception_type"] = type(error).__name__
        record = ResearchError(
            error_type=error_type,
            source=self._source,
            message=message,
            recoverable=True,
            details=payload,
        )
        self._errors.append(record)
        return record

    def drain(self) -> list[ResearchError]:
        """Return every recorded error and clear the log."""
        drained = list(self._errors)
        self._errors.clear()
        return drained
```

- [ ] **Step 4: Write the memory record models**

Create `src/deep_research/memory/entries.py`:

```python
"""Typed memory records shared by the scratchpad, vector, and strategy layers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal, TypeAlias
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from deep_research.utils.types import AwareISOString, ContractModel, UnitScore

MemoryEntryType: TypeAlias = Literal[
    "finding",
    "source_reputation",
    "report_summary",
    "failed_strategy",
]
ScratchpadEntryKind: TypeAlias = Literal[
    "thought",
    "observation",
    "decision",
    "summary",
]
MetadataValue: TypeAlias = str | int | float | bool

_RESERVED_METADATA_KEYS = frozenset(
    {
        "agent_id",
        "confidence",
        "entry_type",
        "session_id",
        "source_title",
        "source_url",
        "timestamp",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_reputation_entry_id(url: str) -> str:
    """Return the stable storage id for one source reputation record."""
    normalized = url.strip()
    if not normalized:
        raise ValueError("source reputation url must not be blank")
    digest = sha256(normalized.encode("utf-8")).hexdigest()
    return f"source_reputation:{digest}"


class MemoryEntry(ContractModel):
    """One durable record stored in long-term semantic memory."""

    entry_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    entry_type: MemoryEntryType
    content: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    confidence: UnitScore = 1.0
    source_url: str | None = Field(default=None, min_length=1)
    source_title: str | None = Field(default=None, min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    attributes: dict[str, MetadataValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_reserved_attributes(self) -> "MemoryEntry":
        collisions = _RESERVED_METADATA_KEYS.intersection(self.attributes)
        if collisions:
            names = ", ".join(sorted(collisions))
            raise ValueError(f"attributes may not reuse reserved metadata keys: {names}")
        return self

    def to_metadata(self) -> dict[str, MetadataValue]:
        """Project this entry onto the flat scalar metadata a vector store accepts."""
        metadata: dict[str, MetadataValue] = {
            "entry_type": self.entry_type,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }
        if self.source_url is not None:
            metadata["source_url"] = self.source_url
        if self.source_title is not None:
            metadata["source_title"] = self.source_title
        metadata.update(self.attributes)
        return metadata

    @classmethod
    def from_storage(
        cls,
        *,
        entry_id: str,
        document: str,
        metadata: Mapping[str, MetadataValue],
    ) -> "MemoryEntry":
        """Rebuild an entry from a stored document and its flat metadata."""
        attributes = {
            key: value
            for key, value in metadata.items()
            if key not in _RESERVED_METADATA_KEYS
        }
        return cls(
            entry_id=entry_id,
            entry_type=metadata["entry_type"],
            content=document,
            session_id=metadata["session_id"],
            agent_id=metadata["agent_id"],
            confidence=float(metadata.get("confidence", 1.0)),
            source_url=metadata.get("source_url"),
            source_title=metadata.get("source_title"),
            timestamp=metadata["timestamp"],
            attributes=attributes,
        )


class MemoryQueryResult(ContractModel):
    """One semantic search hit with its raw backend distance."""

    entry: MemoryEntry
    distance: float | None = Field(default=None, ge=0.0)

    @property
    def relevance(self) -> float:
        """Map an unbounded non-negative distance onto ``(0.0, 1.0]``."""
        if self.distance is None:
            return 1.0
        return 1.0 / (1.0 + self.distance)


class SourceReputation(ContractModel):
    """A running judgement about how much one source can be trusted."""

    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    reputation_score: UnitScore
    observations: int = Field(default=1, ge=1)
    notes: str = ""
    last_updated: AwareISOString = Field(default_factory=_utc_now_iso)

    def to_entry(self, *, session_id: str, agent_id: str) -> MemoryEntry:
        attributes: dict[str, MetadataValue] = {
            "reputation_score": self.reputation_score,
            "observations": self.observations,
        }
        content = f"{self.title} ({self.url})"
        if self.notes:
            attributes["notes"] = self.notes
            content = f"{content}: {self.notes}"
        return MemoryEntry(
            entry_id=source_reputation_entry_id(self.url),
            entry_type="source_reputation",
            content=content,
            session_id=session_id,
            agent_id=agent_id,
            confidence=self.reputation_score,
            source_url=self.url,
            source_title=self.title,
            timestamp=self.last_updated,
            attributes=attributes,
        )

    @classmethod
    def from_entry(cls, entry: MemoryEntry) -> "SourceReputation":
        if entry.entry_type != "source_reputation":
            raise ValueError("entry_type must be source_reputation")
        if entry.source_url is None:
            raise ValueError("source_reputation entries require source_url")
        return cls(
            url=entry.source_url,
            title=entry.source_title or entry.source_url,
            reputation_score=float(
                entry.attributes.get("reputation_score", entry.confidence)
            ),
            observations=int(entry.attributes.get("observations", 1)),
            notes=str(entry.attributes.get("notes", "")),
            last_updated=entry.timestamp,
        )


class ScratchpadEntry(ContractModel):
    """One bounded in-session note written by a single agent."""

    agent_name: str = Field(min_length=1)
    kind: ScratchpadEntryKind = "observation"
    content: str = Field(min_length=1)
    timestamp: AwareISOString = Field(default_factory=_utc_now_iso)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class StrategyRecord(ContractModel):
    """Accumulated outcomes for one topic type in procedural memory."""

    topic_type: str = Field(min_length=1)
    query_templates: list[str] = Field(default_factory=list)
    trusted_source_patterns: list[str] = Field(default_factory=list)
    sessions: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    total_iterations: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)
    updated_at: AwareISOString = Field(default_factory=_utc_now_iso)

    @model_validator(mode="after")
    def validate_counts(self) -> "StrategyRecord":
        if self.successes > self.sessions:
            raise ValueError("successes cannot exceed sessions")
        return self

    @property
    def success_rate(self) -> float:
        return self.successes / self.sessions if self.sessions else 0.0

    @property
    def average_iterations(self) -> float:
        return self.total_iterations / self.sessions if self.sessions else 0.0
```

- [ ] **Step 5: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_memory_entries.py -v
ruff check src/deep_research/memory tests/test_memory_entries.py
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/memory/entries.py src/deep_research/memory/errors.py tests/test_memory_entries.py
git commit -m "feat: add memory record models and error contracts"
```

---

### Task 2: Bounded Scratchpad Memory

**Files:**
- Create: `src/deep_research/memory/scratchpad.py`
- Create: `tests/test_memory_scratchpad.py`

**Interfaces:**
- Consumes: `ScratchpadEntry`, `ScratchpadEntryKind` from `deep_research.memory.entries`; `MemoryErrorLog` from `deep_research.memory.errors`; `ShortTermMemoryConfig` from `deep_research.utils.config`.
- Produces:
  - `Summarizer = Callable[[Sequence[ScratchpadEntry]], str]`
  - `ScratchpadMemory(*, session_id: str, agent_name: str, max_entries: int = 20, summarizer: Summarizer | None = None)`
  - `ScratchpadMemory.from_config(config: ShortTermMemoryConfig, *, session_id: str, agent_name: str, summarizer: Summarizer | None = None) -> ScratchpadMemory`
  - `add(content: str, *, kind: ScratchpadEntryKind = "observation", metadata: Mapping[str, JsonValue] | None = None) -> ScratchpadEntry`
  - `recent(count: int | None = None) -> tuple[ScratchpadEntry, ...]`
  - `entries: tuple[ScratchpadEntry, ...]`, `errors: Sequence[ResearchError]`, `drain_errors() -> list[ResearchError]`, `clear() -> None`, `__len__`

- [ ] **Step 1: Write the failing scratchpad tests**

Create `tests/test_memory_scratchpad.py`:

```python
"""Tests for bounded per-agent, per-session scratchpad memory."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from deep_research.memory.entries import ScratchpadEntry
from deep_research.memory.scratchpad import ScratchpadMemory
from deep_research.utils.config import ShortTermMemoryConfig


def _pad(**overrides: object) -> ScratchpadMemory:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "agent_name": "researcher",
        "max_entries": 4,
    }
    payload.update(overrides)
    return ScratchpadMemory(**payload)  # type: ignore[arg-type]


def test_scratchpad_records_agent_and_kind_on_every_entry() -> None:
    pad = _pad()

    entry = pad.add("Searching for QEC benchmarks.", kind="thought")

    assert entry.agent_name == "researcher"
    assert entry.kind == "thought"
    assert entry.content == "Searching for QEC benchmarks."
    assert pad.entries == (entry,)
    assert len(pad) == 1


def test_scratchpad_defaults_to_observation_entries() -> None:
    pad = _pad()

    assert pad.add("Tool returned 5 results.").kind == "observation"


def test_scratchpad_without_summarizer_slides_the_window() -> None:
    pad = _pad(max_entries=3)
    for index in range(5):
        pad.add(f"note-{index}")

    assert len(pad) == 3
    assert [entry.content for entry in pad.entries] == ["note-2", "note-3", "note-4"]


def test_scratchpad_never_exceeds_its_bound_even_with_a_summarizer() -> None:
    pad = _pad(max_entries=1, summarizer=lambda entries: "summary")
    for index in range(6):
        pad.add(f"note-{index}")

    assert len(pad) == 1
    assert pad.entries[0].content == "note-5"


def test_scratchpad_summarizes_evicted_entries_into_a_summary_entry() -> None:
    seen: list[tuple[str, ...]] = []

    def summarize(entries: Sequence[ScratchpadEntry]) -> str:
        seen.append(tuple(entry.content for entry in entries))
        return "condensed: " + ", ".join(entry.content for entry in entries)

    pad = _pad(max_entries=4, summarizer=summarize)
    for index in range(5):
        pad.add(f"note-{index}")

    assert seen == [("note-0", "note-1")]
    assert [entry.kind for entry in pad.entries] == [
        "summary",
        "observation",
        "observation",
        "observation",
    ]
    assert pad.entries[0].content == "condensed: note-0, note-1"
    assert pad.entries[0].metadata == {"summarized_entries": 2}
    assert [entry.content for entry in pad.entries[1:]] == [
        "note-2",
        "note-3",
        "note-4",
    ]


def test_scratchpad_ignores_a_blank_summary() -> None:
    pad = _pad(max_entries=4, summarizer=lambda entries: "   ")
    for index in range(5):
        pad.add(f"note-{index}")

    assert [entry.content for entry in pad.entries] == [
        "note-2",
        "note-3",
        "note-4",
    ]


def test_scratchpad_records_a_recoverable_error_when_summarization_fails() -> None:
    def explode(entries: Sequence[ScratchpadEntry]) -> str:
        raise RuntimeError("summarizer offline")

    pad = _pad(max_entries=4, summarizer=explode)
    for index in range(5):
        pad.add(f"note-{index}")

    assert len(pad) == 3
    assert [entry.content for entry in pad.entries] == [
        "note-2",
        "note-3",
        "note-4",
    ]
    errors = pad.drain_errors()
    assert len(errors) == 1
    assert errors[0].error_type == "scratchpad_summarization_failed"
    assert errors[0].source == "scratchpad_memory"
    assert errors[0].recoverable is True
    assert errors[0].details["exception_type"] == "RuntimeError"
    assert pad.errors == ()


def test_scratchpad_recent_returns_the_newest_entries_in_order() -> None:
    pad = _pad(max_entries=10)
    for index in range(5):
        pad.add(f"note-{index}")

    assert [entry.content for entry in pad.recent(2)] == ["note-3", "note-4"]
    assert [entry.content for entry in pad.recent(99)] == [
        f"note-{index}" for index in range(5)
    ]
    assert pad.recent(0) == ()
    assert pad.recent() == pad.entries


def test_scratchpad_recent_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="count must not be negative"):
        _pad().recent(-1)


def test_scratchpad_clear_empties_the_window() -> None:
    pad = _pad()
    pad.add("note")

    pad.clear()

    assert pad.entries == ()
    assert len(pad) == 0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"session_id": "  "}, "session_id must not be blank"),
        ({"agent_name": ""}, "agent_name must not be blank"),
        ({"max_entries": 0}, "max_entries must be at least 1"),
    ],
)
def test_scratchpad_rejects_invalid_construction(
    kwargs: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _pad(**kwargs)


def test_scratchpad_from_config_uses_the_short_term_turn_limit() -> None:
    pad = ScratchpadMemory.from_config(
        ShortTermMemoryConfig(max_turns=2),
        session_id="session-1",
        agent_name="planner",
    )
    for index in range(4):
        pad.add(f"note-{index}")

    assert pad.max_entries == 2
    assert len(pad) == 2
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_memory_scratchpad.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.memory.scratchpad'`.

- [ ] **Step 3: Implement the scratchpad**

Create `src/deep_research/memory/scratchpad.py`:

```python
"""Bounded, in-memory, per-agent working memory for a single session.

Scratchpads are never persisted. They hold ReAct summaries, tool observations,
and decisions for one agent inside one session, and they are the fallback
context when long-term memory is unavailable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from pydantic import JsonValue

from deep_research.memory.entries import ScratchpadEntry, ScratchpadEntryKind
from deep_research.memory.errors import MemoryErrorLog
from deep_research.utils.config import ShortTermMemoryConfig
from deep_research.utils.types import ResearchError

Summarizer = Callable[[Sequence[ScratchpadEntry]], str]

_DEFAULT_MAX_ENTRIES = 20


class ScratchpadMemory:
    """A sliding window of agent notes with an optional summarization hook."""

    def __init__(
        self,
        *,
        session_id: str,
        agent_name: str,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        summarizer: Summarizer | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("session_id must not be blank")
        if not agent_name.strip():
            raise ValueError("agent_name must not be blank")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.session_id = session_id.strip()
        self.agent_name = agent_name.strip()
        self.max_entries = max_entries
        self._summarizer = summarizer
        self._entries: list[ScratchpadEntry] = []
        self._error_log = MemoryErrorLog("scratchpad_memory")

    @classmethod
    def from_config(
        cls,
        config: ShortTermMemoryConfig,
        *,
        session_id: str,
        agent_name: str,
        summarizer: Summarizer | None = None,
    ) -> "ScratchpadMemory":
        return cls(
            session_id=session_id,
            agent_name=agent_name,
            max_entries=config.max_turns,
            summarizer=summarizer,
        )

    @property
    def entries(self) -> tuple[ScratchpadEntry, ...]:
        return tuple(self._entries)

    @property
    def errors(self) -> Sequence[ResearchError]:
        return self._error_log.errors

    def drain_errors(self) -> list[ResearchError]:
        """Return and clear recoverable errors for merging into research state."""
        return self._error_log.drain()

    def __len__(self) -> int:
        return len(self._entries)

    def add(
        self,
        content: str,
        *,
        kind: ScratchpadEntryKind = "observation",
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> ScratchpadEntry:
        """Append one note, compacting the window first when it is full."""
        entry = ScratchpadEntry(
            agent_name=self.agent_name,
            kind=kind,
            content=content,
            metadata=dict(metadata or {}),
        )
        if len(self._entries) >= self.max_entries:
            self._compact()
        self._entries.append(entry)
        self._enforce_bound()
        return entry

    def recent(self, count: int | None = None) -> tuple[ScratchpadEntry, ...]:
        """Return the newest ``count`` entries, oldest first."""
        if count is None:
            return tuple(self._entries)
        if count < 0:
            raise ValueError("count must not be negative")
        if count == 0:
            return ()
        return tuple(self._entries[-count:])

    def clear(self) -> None:
        self._entries.clear()

    def _compact(self) -> None:
        evict_count = max(1, self.max_entries // 2)
        evicted = self._entries[:evict_count]
        self._entries = self._entries[evict_count:]
        if self._summarizer is None or not evicted:
            return
        try:
            summary = self._summarizer(tuple(evicted))
        except Exception as error:
            self._error_log.record(
                error_type="scratchpad_summarization_failed",
                message=(
                    "Scratchpad summarization failed; evicted entries were dropped."
                ),
                error=error,
                details={
                    "session_id": self.session_id,
                    "agent_name": self.agent_name,
                    "evicted_entries": len(evicted),
                },
            )
            return
        if not isinstance(summary, str) or not summary.strip():
            return
        self._entries.insert(
            0,
            ScratchpadEntry(
                agent_name=self.agent_name,
                kind="summary",
                content=summary.strip(),
                metadata={"summarized_entries": len(evicted)},
            ),
        )

    def _enforce_bound(self) -> None:
        overflow = len(self._entries) - self.max_entries
        if overflow > 0:
            del self._entries[:overflow]
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_memory_scratchpad.py -v
ruff check src/deep_research/memory tests/test_memory_scratchpad.py
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add src/deep_research/memory/scratchpad.py tests/test_memory_scratchpad.py
git commit -m "feat: add bounded scratchpad memory"
```

---

### Task 3: Memory Observability Spans

**Files:**
- Modify: `src/deep_research/observability/metrics.py:85-87`
- Modify: `src/deep_research/observability/tracker.py:45` (`SpanKind`), `:35-41` (metric imports), `:166-187` (`SpanHandle`), `:397-436` (after `tool_span`)
- Modify: `src/deep_research/observability/__init__.py:11-17,20-35`
- Create: `src/deep_research/memory/instrumentation.py`
- Modify: `tests/test_observability_metrics.py` (append)
- Modify: `tests/test_observability_tracker.py` (append)
- Create: `tests/test_memory_instrumentation.py`

**Interfaces:**
- Consumes: the existing `Tracker._span` engine, `TraceContext`, `current_trace_context`, `OutcomeMetric`.
- Produces:
  - `MemoryLayer = Literal["scratchpad", "long_term", "procedural"]` in `deep_research.observability.metrics`
  - `MemoryMetric(metric_type="memory", session_id, agent_name, memory_layer, operation, entry_type, top_k, result_count, latency_ms, success, error_type)`
  - `SpanHandle.result_count: int | None` and `SpanHandle.set_result_count(count: int) -> None`
  - `Tracker.memory_span(operation: str, *, memory_layer: MemoryLayer, entry_type: str | None = None, top_k: int | None = None) -> AbstractAsyncContextManager[SpanHandle]`
  - `deep_research.memory.instrumentation.memory_operation(tracker: Tracker | None, operation: str, *, memory_layer: MemoryLayer, entry_type: str | None = None, top_k: int | None = None) -> AsyncIterator[MemoryOperationHandle]` (an `@asynccontextmanager`)
  - `MemoryOperationHandle` protocol with `set_result_count(count: int) -> None`

- [ ] **Step 1: Write the failing metric schema tests**

Append to `tests/test_observability_metrics.py`:

```python
def test_memory_metric_captures_operation_shape() -> None:
    from deep_research.observability.metrics import MemoryMetric

    metric = MemoryMetric(
        session_id="session-1",
        agent_name="researcher",
        memory_layer="long_term",
        operation="query",
        entry_type="finding",
        top_k=5,
        result_count=3,
        latency_ms=12.5,
        success=True,
    )

    assert metric.metric_type == "memory"
    assert metric.model_dump()["memory_layer"] == "long_term"


def test_memory_metric_defaults_result_count_to_zero() -> None:
    from deep_research.observability.metrics import MemoryMetric

    metric = MemoryMetric(
        session_id="session-1",
        memory_layer="procedural",
        operation="save",
        latency_ms=1.0,
        success=False,
        error_type="OSError",
    )

    assert metric.result_count == 0
    assert metric.agent_name is None
    assert metric.top_k is None


def test_memory_metric_rejects_unknown_layers_and_non_positive_top_k() -> None:
    from pydantic import ValidationError

    from deep_research.observability.metrics import MemoryMetric

    with pytest.raises(ValidationError):
        MemoryMetric(
            session_id="session-1",
            memory_layer="redis",
            operation="save",
            latency_ms=1.0,
            success=True,
        )
    with pytest.raises(ValidationError):
        MemoryMetric(
            session_id="session-1",
            memory_layer="long_term",
            operation="query",
            top_k=0,
            latency_ms=1.0,
            success=True,
        )
```

`tests/test_observability_metrics.py` already imports `pytest` at the top of the file, so no import change is needed.

- [ ] **Step 2: Write the failing tracker span tests**

Append to `tests/test_observability_tracker.py`:

```python
@pytest.mark.asyncio
async def test_memory_span_records_operation_metadata_and_result_count() -> None:
    tracker = Tracker(
        LangSmithRuntimeConfig(tracing_enabled=False),
        client_factory=ForbiddenClientFactory(),
        trace_factory=ForbiddenTraceFactory(),
    )

    async with tracker.session_span("session-memory", "What changed?"):
        async with tracker.agent_span("researcher"):
            async with tracker.memory_span(
                "query",
                memory_layer="long_term",
                entry_type="finding",
                top_k=5,
            ) as span:
                context = current_trace_context()
                assert context is not None
                assert context.tool_name == "memory.long_term"
                span.set_result_count(3)

    memory_metrics = [
        metric for metric in tracker.metrics if metric.metric_type == "memory"
    ]
    assert len(memory_metrics) == 1
    metric = memory_metrics[0]
    assert metric.session_id == "session-memory"
    assert metric.agent_name == "researcher"
    assert metric.memory_layer == "long_term"
    assert metric.operation == "query"
    assert metric.entry_type == "finding"
    assert metric.top_k == 5
    assert metric.result_count == 3
    assert metric.success is True
    assert metric.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_memory_span_records_storage_errors_and_reraises() -> None:
    tracker = Tracker(
        LangSmithRuntimeConfig(tracing_enabled=False),
        client_factory=ForbiddenClientFactory(),
        trace_factory=ForbiddenTraceFactory(),
    )

    with pytest.raises(OSError):
        async with tracker.session_span("session-memory", "What changed?"):
            async with tracker.memory_span("save", memory_layer="procedural"):
                raise OSError("disk full")

    metric = next(
        metric for metric in tracker.metrics if metric.metric_type == "memory"
    )
    assert metric.success is False
    assert metric.error_type == "OSError"
    assert metric.result_count == 0


@pytest.mark.asyncio
async def test_memory_span_requires_an_active_session_span() -> None:
    tracker = Tracker(
        LangSmithRuntimeConfig(tracing_enabled=False),
        client_factory=ForbiddenClientFactory(),
        trace_factory=ForbiddenTraceFactory(),
    )

    with pytest.raises(RuntimeError, match="child spans require an active session"):
        tracker.memory_span("save", memory_layer="long_term")
```

- [ ] **Step 3: Run both test files and verify they fail**

Run:

```bash
python -m pytest tests/test_observability_metrics.py tests/test_observability_tracker.py -v
```

Expected: the new tests fail with `ImportError: cannot import name 'MemoryMetric'` and `AttributeError: 'Tracker' object has no attribute 'memory_span'`. Existing tests still pass.

- [ ] **Step 4: Add the memory metric schema**

In `src/deep_research/observability/metrics.py`, add after the `TokenUsageMetric` class:

```python
MemoryLayer: TypeAlias = Literal["scratchpad", "long_term", "procedural"]


class MemoryMetric(OutcomeMetric):
    metric_type: Literal["memory"] = "memory"
    session_id: str = Field(min_length=1)
    agent_name: str | None = Field(default=None, min_length=1)
    memory_layer: MemoryLayer
    operation: str = Field(min_length=1)
    entry_type: str | None = Field(default=None, min_length=1)
    top_k: int | None = Field(default=None, ge=1)
    result_count: NonNegativeInt = 0
```

Replace the `MetricRecord` alias at the end of the file:

```python
MetricRecord: TypeAlias = (
    SessionMetric | AgentMetric | ToolMetric | TokenUsageMetric | MemoryMetric
)
```

- [ ] **Step 5: Add the memory span to the tracker**

In `src/deep_research/observability/tracker.py`, replace:

```python
SpanKind: TypeAlias = Literal["session", "agent", "react_iteration", "llm", "tool"]
```

with:

```python
SpanKind: TypeAlias = Literal[
    "session",
    "agent",
    "react_iteration",
    "llm",
    "tool",
    "memory",
]
```

Replace the metrics import block:

```python
from deep_research.observability.metrics import (
    AgentMetric,
    MetricRecord,
    SessionMetric,
    TokenUsageMetric,
    ToolMetric,
)
```

with:

```python
from deep_research.observability.metrics import (
    AgentMetric,
    MemoryLayer,
    MemoryMetric,
    MetricRecord,
    SessionMetric,
    TokenUsageMetric,
    ToolMetric,
)
```

Replace the `SpanHandle` dataclass body:

```python
@dataclass(slots=True)
class SpanHandle:
    context: TraceContext
    trace_url: str | None = None
    outputs: dict[str, JsonValue] | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
```

with:

```python
@dataclass(slots=True)
class SpanHandle:
    context: TraceContext
    trace_url: str | None = None
    outputs: dict[str, JsonValue] | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    result_count: int | None = None

    def set_result_count(self, count: int) -> None:
        if count < 0:
            raise ValueError("result_count must not be negative")
        self.result_count = count
```

Insert this method immediately after `tool_span` and before `_require_context`:

```python
    def memory_span(
        self,
        operation: str,
        *,
        memory_layer: MemoryLayer,
        entry_type: str | None = None,
        top_k: int | None = None,
    ) -> AbstractAsyncContextManager[SpanHandle]:
        parent = self._require_context()
        operation = _validate_non_empty_string(operation)
        context = _validated_context(
            parent, tool_name=f"memory.{memory_layer}", model=None
        )
        inputs: dict[str, JsonValue] = {
            "operation": operation,
            "memory_layer": memory_layer,
        }
        if entry_type is not None:
            inputs["entry_type"] = entry_type
        if top_k is not None:
            inputs["top_k"] = top_k

        def metric_factory(
            ctx: TraceContext,
            latency: float,
            success: bool,
            error_type: str | None,
            trace_url: str | None,
            handle: SpanHandle,
        ) -> MetricRecord:
            del trace_url
            return MemoryMetric(
                session_id=ctx.session_id,
                agent_name=ctx.agent_name,
                memory_layer=memory_layer,
                operation=operation,
                entry_type=entry_type,
                top_k=top_k,
                result_count=handle.result_count or 0,
                latency_ms=latency,
                success=success,
                error_type=error_type,
            )

        return self._span(
            kind="memory",
            name=f"memory.{memory_layer}.{operation}",
            run_type="tool",
            context=context,
            inputs=inputs,
            metric_factory=metric_factory,
        )
```

- [ ] **Step 6: Export the new observability contracts**

In `src/deep_research/observability/__init__.py`, replace the metrics import block and `__all__` so both include the new names:

```python
from deep_research.observability.metrics import (
    AgentMetric,
    MemoryLayer,
    MemoryMetric,
    MetricRecord,
    SessionMetric,
    TokenUsageMetric,
    ToolMetric,
)
```

and add `"MemoryLayer",` and `"MemoryMetric",` to `__all__`, keeping it alphabetically sorted (they go directly after `"LangSmithRuntimeConfig",`).

- [ ] **Step 7: Run the observability tests and verify they pass**

Run:

```bash
python -m pytest tests/test_observability_metrics.py tests/test_observability_tracker.py -v
```

Expected: all tests pass, including the three new tracker tests and three new metric tests.

- [ ] **Step 8: Write the failing instrumentation wrapper tests**

Create `tests/test_memory_instrumentation.py`:

```python
"""Tests for the optional observability wrapper around memory operations."""

from __future__ import annotations

import pytest

from deep_research.memory.instrumentation import memory_operation
from deep_research.observability import LangSmithRuntimeConfig, Tracker


def _tracker() -> Tracker:
    return Tracker(LangSmithRuntimeConfig(tracing_enabled=False))


@pytest.mark.asyncio
async def test_memory_operation_without_a_tracker_is_a_no_op() -> None:
    async with memory_operation(
        None, "query", memory_layer="long_term", top_k=3
    ) as span:
        span.set_result_count(2)


@pytest.mark.asyncio
async def test_memory_operation_outside_a_session_emits_no_metric() -> None:
    tracker = _tracker()

    async with memory_operation(tracker, "load", memory_layer="procedural") as span:
        span.set_result_count(1)

    assert tracker.metrics == ()


@pytest.mark.asyncio
async def test_memory_operation_inside_a_session_emits_a_memory_metric() -> None:
    tracker = _tracker()

    async with tracker.session_span("session-1", "Why?"):
        async with memory_operation(
            tracker,
            "query",
            memory_layer="long_term",
            entry_type="finding",
            top_k=4,
        ) as span:
            span.set_result_count(2)

    metric = next(
        metric for metric in tracker.metrics if metric.metric_type == "memory"
    )
    assert metric.operation == "query"
    assert metric.top_k == 4
    assert metric.result_count == 2
    assert metric.success is True


@pytest.mark.asyncio
async def test_memory_operation_lets_failures_reach_the_span() -> None:
    tracker = _tracker()

    with pytest.raises(RuntimeError):
        async with tracker.session_span("session-1", "Why?"):
            async with memory_operation(tracker, "save", memory_layer="long_term"):
                raise RuntimeError("backend down")

    metric = next(
        metric for metric in tracker.metrics if metric.metric_type == "memory"
    )
    assert metric.success is False
    assert metric.error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_no_op_handle_rejects_negative_result_counts() -> None:
    async with memory_operation(None, "save", memory_layer="scratchpad") as span:
        with pytest.raises(ValueError, match="result_count must not be negative"):
            span.set_result_count(-1)
```

- [ ] **Step 9: Run the instrumentation tests and verify they fail**

Run:

```bash
python -m pytest tests/test_memory_instrumentation.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.memory.instrumentation'`.

- [ ] **Step 10: Implement the instrumentation wrapper**

Create `src/deep_research/memory/instrumentation.py`:

```python
"""Optional observability wrapper for memory operations.

Memory layers are constructed at startup, before any session span exists, so
they must tolerate a missing tracker and a missing trace context. This wrapper
delegates to ``Tracker.memory_span`` when both are present and degrades to a
no-op handle otherwise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from deep_research.observability import MemoryLayer, Tracker, current_trace_context


class MemoryOperationHandle(Protocol):
    def set_result_count(self, count: int) -> None:
        """Record how many entries the operation read or wrote."""
        raise NotImplementedError


class _NullMemoryOperation:
    __slots__ = ("result_count",)

    def __init__(self) -> None:
        self.result_count = 0

    def set_result_count(self, count: int) -> None:
        if count < 0:
            raise ValueError("result_count must not be negative")
        self.result_count = count


@asynccontextmanager
async def memory_operation(
    tracker: Tracker | None,
    operation: str,
    *,
    memory_layer: MemoryLayer,
    entry_type: str | None = None,
    top_k: int | None = None,
) -> AsyncIterator[MemoryOperationHandle]:
    """Open a memory observability span when one can be recorded."""
    if tracker is None or current_trace_context() is None:
        yield _NullMemoryOperation()
        return
    async with tracker.memory_span(
        operation,
        memory_layer=memory_layer,
        entry_type=entry_type,
        top_k=top_k,
    ) as handle:
        yield handle
```

- [ ] **Step 11: Run the instrumentation tests and verify they pass**

Run:

```bash
python -m pytest tests/test_memory_instrumentation.py tests/test_observability_metrics.py tests/test_observability_tracker.py -v
ruff check src/deep_research tests
```

Expected: all tests pass and Ruff prints `All checks passed!`.

- [ ] **Step 12: Commit**

```bash
git add src/deep_research/observability src/deep_research/memory/instrumentation.py tests/test_observability_metrics.py tests/test_observability_tracker.py tests/test_memory_instrumentation.py
git commit -m "feat: add memory observability spans"
```

---

### Task 4: Procedural Strategy Registry

**Files:**
- Modify: `src/deep_research/utils/config.py:41-52,71-85`
- Modify: `config.yaml:20-25`
- Modify: `tests/test_config.py:29-33,95-105`
- Create: `src/deep_research/memory/procedural.py`
- Create: `tests/test_memory_procedural.py`

**Interfaces:**
- Consumes: `StrategyRecord` from `deep_research.memory.entries`; `MemoryErrorLog`, `MemoryInitializationError` from `deep_research.memory.errors`; `memory_operation` from `deep_research.memory.instrumentation`; `Tracker` from `deep_research.observability`.
- Produces:
  - `ProceduralMemoryConfig(strategies_path: str = "memory/strategies.json")` on `MemoryConfig.procedural`
  - `ProceduralMemory(path: Path | str, *, tracker: Tracker | None = None)`
  - `ProceduralMemory.from_config(config: ProceduralMemoryConfig, *, tracker: Tracker | None = None) -> ProceduralMemory`
  - `async load() -> None`
  - `async save() -> bool`
  - `async record_session_outcome(*, topic_type: str, succeeded: bool, iterations: int, query_templates: Iterable[str] = (), trusted_source_patterns: Iterable[str] = (), note: str | None = None) -> StrategyRecord`
  - `strategies: tuple[StrategyRecord, ...]`, `get(topic_type: str) -> StrategyRecord | None`, `path: Path`, `loaded: bool`, `errors: Sequence[ResearchError]`, `drain_errors() -> list[ResearchError]`

- [ ] **Step 1: Write the failing configuration tests**

In `tests/test_config.py`, inside the `config_path` fixture, replace:

```python
                    "short_term": {"max_turns": 20},
```

with:

```python
                    "short_term": {"max_turns": 20},
                    "procedural": {"strategies_path": "memory/strategies.json"},
```

In the `test_environment_overrides_every_yaml_leaf` parametrize list, add this entry directly after the `MEMORY_SHORT_TERM_MAX_TURNS` entry:

```python
        (
            "MEMORY_PROCEDURAL_STRATEGIES_PATH",
            ("memory", "procedural", "strategies_path"),
            "env-memory/strategies.json",
            "env-memory/strategies.json",
        ),
```

Append this test to `tests/test_config.py`:

```python
def test_procedural_memory_path_defaults_to_the_runtime_registry(
    config_path: Path,
) -> None:
    settings = load_config(str(config_path))

    assert settings.memory.procedural.strategies_path == "memory/strategies.json"
```

- [ ] **Step 2: Write the failing procedural memory tests**

Create `tests/test_memory_procedural.py`:

```python
"""Tests for the JSON-backed procedural strategy registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import deep_research.memory.procedural as procedural_module
from deep_research.memory.errors import MemoryInitializationError
from deep_research.memory.procedural import ProceduralMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from deep_research.utils.config import ProceduralMemoryConfig


@pytest.fixture
def strategies_path(tmp_path: Path) -> Path:
    return tmp_path / "memory" / "strategies.json"


@pytest.mark.asyncio
async def test_load_starts_empty_when_no_registry_file_exists(
    strategies_path: Path,
) -> None:
    memory = ProceduralMemory(strategies_path)

    await memory.load()

    assert memory.loaded is True
    assert memory.strategies == ()
    assert memory.errors == ()
    assert not strategies_path.exists()


@pytest.mark.asyncio
async def test_record_session_outcome_persists_a_new_strategy(
    strategies_path: Path,
) -> None:
    memory = ProceduralMemory(strategies_path)
    await memory.load()

    record = await memory.record_session_outcome(
        topic_type="technology",
        succeeded=True,
        iterations=3,
        query_templates=["{topic} benchmark 2026"],
        trusted_source_patterns=["*.edu"],
        note="Vendor blogs were unreliable.",
    )

    assert record.sessions == 1
    assert record.successes == 1
    assert record.success_rate == 1.0
    assert record.average_iterations == 3.0
    assert strategies_path.exists()

    reloaded = ProceduralMemory(strategies_path)
    await reloaded.load()
    assert reloaded.get("technology") == record


@pytest.mark.asyncio
async def test_repeated_outcomes_accumulate_counts_and_merge_lists(
    strategies_path: Path,
) -> None:
    memory = ProceduralMemory(strategies_path)
    await memory.load()

    await memory.record_session_outcome(
        topic_type="technology",
        succeeded=True,
        iterations=2,
        query_templates=["{topic} benchmark 2026"],
        note="Vendor blogs were unreliable.",
    )
    record = await memory.record_session_outcome(
        topic_type="technology",
        succeeded=False,
        iterations=4,
        query_templates=["{topic} benchmark 2026", "{topic} peer review"],
        note="Vendor blogs were unreliable.",
    )

    assert record.sessions == 2
    assert record.successes == 1
    assert record.success_rate == 0.5
    assert record.average_iterations == 3.0
    assert record.query_templates == [
        "{topic} benchmark 2026",
        "{topic} peer review",
    ]
    assert record.notes == ["Vendor blogs were unreliable."]
    assert len(memory.strategies) == 1


@pytest.mark.asyncio
async def test_save_writes_sorted_json_that_reloads_cleanly(
    strategies_path: Path,
) -> None:
    memory = ProceduralMemory(strategies_path)
    await memory.load()
    await memory.record_session_outcome(
        topic_type="science", succeeded=True, iterations=1
    )
    await memory.record_session_outcome(
        topic_type="finance", succeeded=False, iterations=5
    )

    assert await memory.save() is True

    payload = json.loads(strategies_path.read_text(encoding="utf-8"))
    assert [item["topic_type"] for item in payload] == ["finance", "science"]


@pytest.mark.asyncio
async def test_corrupt_registry_is_backed_up_and_restarts_empty(
    strategies_path: Path,
) -> None:
    strategies_path.parent.mkdir(parents=True, exist_ok=True)
    strategies_path.write_text("{ not json", encoding="utf-8")
    memory = ProceduralMemory(strategies_path)

    await memory.load()

    backups = list(strategies_path.parent.glob("strategies.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ not json"
    assert not strategies_path.exists()
    assert memory.strategies == ()

    errors = memory.drain_errors()
    assert len(errors) == 1
    assert errors[0].error_type == "procedural_memory_corrupt"
    assert errors[0].recoverable is True
    assert errors[0].details["backup_path"] == str(backups[0])


@pytest.mark.asyncio
async def test_schema_violations_are_treated_as_corruption(
    strategies_path: Path,
) -> None:
    strategies_path.parent.mkdir(parents=True, exist_ok=True)
    strategies_path.write_text(
        json.dumps([{"topic_type": "technology", "sessions": -4}]),
        encoding="utf-8",
    )
    memory = ProceduralMemory(strategies_path)

    await memory.load()

    assert memory.strategies == ()
    assert list(strategies_path.parent.glob("strategies.json.corrupt-*.bak"))
    assert memory.drain_errors()[0].error_type == "procedural_memory_corrupt"


@pytest.mark.asyncio
async def test_unreadable_registry_fails_startup(
    strategies_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategies_path.parent.mkdir(parents=True, exist_ok=True)
    strategies_path.write_text("[]", encoding="utf-8")

    def explode(self: Path, encoding: str = "utf-8") -> str:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", explode)
    memory = ProceduralMemory(strategies_path)

    with pytest.raises(MemoryInitializationError, match="cannot read"):
        await memory.load()


@pytest.mark.asyncio
async def test_write_failures_are_recoverable(
    strategies_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = ProceduralMemory(strategies_path)
    await memory.load()

    def explode(path: Path, payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(procedural_module, "_write_json_atomic", explode)

    record = await memory.record_session_outcome(
        topic_type="technology", succeeded=True, iterations=1
    )

    assert record.sessions == 1
    assert memory.get("technology") == record
    errors = memory.drain_errors()
    assert len(errors) == 1
    assert errors[0].error_type == "procedural_memory_write_failed"
    assert errors[0].details["exception_type"] == "OSError"
    assert await memory.save() is False


@pytest.mark.asyncio
async def test_operations_emit_memory_metrics_inside_a_session(
    strategies_path: Path,
) -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    memory = ProceduralMemory(strategies_path, tracker=tracker)

    async with tracker.session_span("session-1", "Why?"):
        await memory.load()
        await memory.record_session_outcome(
            topic_type="technology", succeeded=True, iterations=1
        )

    operations = [
        metric.operation
        for metric in tracker.metrics
        if metric.metric_type == "memory"
    ]
    assert operations == ["load", "record_session_outcome"]
    assert all(
        metric.memory_layer == "procedural"
        for metric in tracker.metrics
        if metric.metric_type == "memory"
    )


@pytest.mark.asyncio
async def test_record_session_outcome_rejects_negative_iterations(
    strategies_path: Path,
) -> None:
    memory = ProceduralMemory(strategies_path)
    await memory.load()

    with pytest.raises(ValueError, match="iterations must not be negative"):
        await memory.record_session_outcome(
            topic_type="technology", succeeded=True, iterations=-1
        )


def test_from_config_uses_the_configured_registry_path(tmp_path: Path) -> None:
    config = ProceduralMemoryConfig(
        strategies_path=str(tmp_path / "strategies.json")
    )

    memory = ProceduralMemory.from_config(config)

    assert memory.path == tmp_path / "strategies.json"
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_config.py tests/test_memory_procedural.py -v
```

Expected: `tests/test_memory_procedural.py` fails collection with `ModuleNotFoundError: No module named 'deep_research.memory.procedural'`, and the new config tests fail with `ImportError: cannot import name 'ProceduralMemoryConfig'` / an override assertion error.

- [ ] **Step 4: Add the procedural memory configuration**

In `src/deep_research/utils/config.py`, add after `ShortTermMemoryConfig`:

```python
class ProceduralMemoryConfig(BaseModel):
    """Procedural strategy registry settings."""

    strategies_path: str = "memory/strategies.json"
```

Replace the `MemoryConfig` body:

```python
class MemoryConfig(BaseModel):
    """Memory settings."""

    long_term: LongTermMemoryConfig = LongTermMemoryConfig()
    short_term: ShortTermMemoryConfig = ShortTermMemoryConfig()
    procedural: ProceduralMemoryConfig = ProceduralMemoryConfig()
```

In `_ENVIRONMENT_OVERRIDES`, add after the `MEMORY_SHORT_TERM_MAX_TURNS` entry:

```python
    "MEMORY_PROCEDURAL_STRATEGIES_PATH": (
        "memory",
        "procedural",
        "strategies_path",
    ),
```

In `config.yaml`, replace:

```yaml
  short_term:
    max_turns: 20
```

with:

```yaml
  short_term:
    max_turns: 20
  procedural:
    strategies_path: memory/strategies.json
```

- [ ] **Step 5: Implement the procedural registry**

Create `src/deep_research/memory/procedural.py`:

```python
"""JSON-backed procedural memory: which research strategies actually worked."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from deep_research.memory.entries import StrategyRecord
from deep_research.memory.errors import MemoryErrorLog, MemoryInitializationError
from deep_research.memory.instrumentation import memory_operation
from deep_research.observability import Tracker
from deep_research.utils.config import ProceduralMemoryConfig
from deep_research.utils.types import ResearchError

_STRATEGY_LIST_ADAPTER = TypeAdapter(list[StrategyRecord])
_MAX_NOTES = 50


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_unique(existing: Sequence[str], additions: Iterable[str]) -> list[str]:
    """Append new non-blank values, preserving order and dropping duplicates."""
    merged = list(existing)
    seen = set(merged)
    for value in additions:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            merged.append(cleaned)
            seen.add(cleaned)
    return merged


def _write_json_atomic(path: Path, payload: list[dict[str, Any]]) -> None:
    """Write JSON through a temporary file so a crash cannot truncate the registry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


class ProceduralMemory:
    """A strategy registry keyed by topic type, persisted as a JSON list."""

    def __init__(self, path: Path | str, *, tracker: Tracker | None = None) -> None:
        self._path = Path(path)
        self._tracker = tracker
        self._strategies: dict[str, StrategyRecord] = {}
        self._error_log = MemoryErrorLog("procedural_memory")
        self._loaded = False

    @classmethod
    def from_config(
        cls,
        config: ProceduralMemoryConfig,
        *,
        tracker: Tracker | None = None,
    ) -> "ProceduralMemory":
        return cls(config.strategies_path, tracker=tracker)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def strategies(self) -> tuple[StrategyRecord, ...]:
        return tuple(self._sorted_strategies())

    @property
    def errors(self) -> Sequence[ResearchError]:
        return self._error_log.errors

    def drain_errors(self) -> list[ResearchError]:
        return self._error_log.drain()

    def get(self, topic_type: str) -> StrategyRecord | None:
        return self._strategies.get(topic_type.strip())

    async def load(self) -> None:
        """Read the registry from disk, quarantining a corrupt file."""
        async with memory_operation(
            self._tracker, "load", memory_layer="procedural"
        ) as span:
            records = await asyncio.to_thread(self._read_strategies)
            self._strategies = {record.topic_type: record for record in records}
            self._loaded = True
            span.set_result_count(len(self._strategies))

    async def save(self) -> bool:
        """Persist the registry. Returns False on a recoverable write failure."""
        try:
            async with memory_operation(
                self._tracker, "save", memory_layer="procedural"
            ) as span:
                await self._write_strategies()
                span.set_result_count(len(self._strategies))
        except OSError as error:
            self._record_write_failure(error)
            return False
        return True

    async def record_session_outcome(
        self,
        *,
        topic_type: str,
        succeeded: bool,
        iterations: int,
        query_templates: Iterable[str] = (),
        trusted_source_patterns: Iterable[str] = (),
        note: str | None = None,
    ) -> StrategyRecord:
        """Fold one completed session into the strategy for ``topic_type``.

        The in-memory registry is always updated. A failure to persist is
        recoverable and recorded as a research error.
        """
        if iterations < 0:
            raise ValueError("iterations must not be negative")
        cleaned_topic = topic_type.strip()
        if not cleaned_topic:
            raise ValueError("topic_type must not be blank")

        base = self._strategies.get(cleaned_topic) or StrategyRecord(
            topic_type=cleaned_topic
        )
        updated = StrategyRecord(
            topic_type=cleaned_topic,
            query_templates=_merge_unique(base.query_templates, query_templates),
            trusted_source_patterns=_merge_unique(
                base.trusted_source_patterns, trusted_source_patterns
            ),
            sessions=base.sessions + 1,
            successes=base.successes + (1 if succeeded else 0),
            total_iterations=base.total_iterations + iterations,
            notes=_merge_unique(base.notes, [note] if note else [])[-_MAX_NOTES:],
            updated_at=_utc_now_iso(),
        )
        self._strategies[cleaned_topic] = updated

        try:
            async with memory_operation(
                self._tracker,
                "record_session_outcome",
                memory_layer="procedural",
                entry_type=cleaned_topic,
            ) as span:
                await self._write_strategies()
                span.set_result_count(1)
        except OSError as error:
            self._record_write_failure(error)
        return updated

    def _sorted_strategies(self) -> list[StrategyRecord]:
        return [self._strategies[key] for key in sorted(self._strategies)]

    async def _write_strategies(self) -> None:
        payload = [
            record.model_dump(mode="json") for record in self._sorted_strategies()
        ]
        await asyncio.to_thread(_write_json_atomic, self._path, payload)

    def _record_write_failure(self, error: OSError) -> None:
        self._error_log.record(
            error_type="procedural_memory_write_failed",
            message=(
                "Procedural memory could not be written; "
                "in-memory strategies are unchanged."
            ),
            error=error,
            details={"path": str(self._path)},
        )

    def _read_strategies(self) -> list[StrategyRecord]:
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as error:
            raise MemoryInitializationError(
                f"cannot read procedural memory at {self._path}: "
                f"{type(error).__name__}"
            ) from error
        try:
            return _STRATEGY_LIST_ADAPTER.validate_python(json.loads(raw))
        except (ValueError, TypeError, ValidationError) as error:
            backup = self._quarantine_corrupt_file()
            self._error_log.record(
                error_type="procedural_memory_corrupt",
                message=(
                    "Procedural memory was corrupt; it was backed up and the "
                    "registry restarted empty."
                ),
                error=error,
                details={"path": str(self._path), "backup_path": str(backup)},
            )
            return []

    def _quarantine_corrupt_file(self) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate = self._path.with_name(f"{self._path.name}.corrupt-{stamp}.bak")
        suffix = 1
        while candidate.exists():
            candidate = self._path.with_name(
                f"{self._path.name}.corrupt-{stamp}-{suffix}.bak"
            )
            suffix += 1
        try:
            os.replace(self._path, candidate)
        except OSError as error:
            raise MemoryInitializationError(
                f"cannot back up corrupt procedural memory at {self._path}: "
                f"{type(error).__name__}"
            ) from error
        return candidate
```

Note: `json.JSONDecodeError` subclasses `ValueError`, so the single `ValueError` clause covers both malformed JSON and Pydantic validation failures.

- [ ] **Step 6: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_config.py tests/test_memory_procedural.py -v
ruff check src/deep_research tests
```

Expected: all config and procedural tests pass and Ruff prints `All checks passed!`.

- [ ] **Step 7: Confirm no test touched the runtime registry**

Run:

```bash
git status --short
ls memory 2>/dev/null || echo "no runtime memory directory"
```

Expected: only the intended source/test/config files appear as modified, and no `memory/` directory was created in the repository.

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/memory/procedural.py src/deep_research/utils/config.py config.yaml tests/test_memory_procedural.py tests/test_config.py
git commit -m "feat: add procedural strategy memory"
```

---

### Task 5: Long-Term Memory Core Behavior

**Files:**
- Create: `src/deep_research/memory/long_term.py`
- Create: `tests/memory_fakes.py`
- Create: `tests/test_memory_long_term.py`

**Interfaces:**
- Consumes: `MemoryEntry`, `MemoryEntryType`, `MemoryQueryResult`, `SourceReputation`, `source_reputation_entry_id` from `deep_research.memory.entries`; `MemoryErrorLog` from `deep_research.memory.errors`; `memory_operation` from `deep_research.memory.instrumentation`; `Tracker` from `deep_research.observability`. (`MemoryInitializationError` and `LongTermMemoryConfig` are added to this module in Task 7, not here.)
- Produces:
  - `EmbeddingProvider` protocol: `embed_query(text: str) -> list[float]`, `embed_documents(texts: Sequence[str]) -> list[list[float]]`
  - `VectorCollection` protocol: `upsert(*, ids, documents, embeddings, metadatas) -> None`, `query(*, query_embeddings, n_results, where=None, include=None) -> Mapping[str, Any]`, `get(*, ids, include=None) -> Mapping[str, Any]`, `count() -> int`
  - `LongTermMemory(*, collection: VectorCollection, embeddings: EmbeddingProvider, tracker: Tracker | None = None)`
  - `async save(entry: MemoryEntry) -> bool`
  - `async save_many(entries: Sequence[MemoryEntry]) -> int`
  - `async query(text: str, *, top_k: int = 5, entry_type: MemoryEntryType | None = None, where: Mapping[str, Any] | None = None) -> list[MemoryQueryResult]`
  - `async get_source_reputation(url: str) -> SourceReputation | None`
  - `async update_source_reputation(*, url: str, title: str, reputation_score: float, session_id: str, agent_id: str, notes: str = "") -> SourceReputation | None`
  - `errors: Sequence[ResearchError]`, `drain_errors() -> list[ResearchError]`
  - `tests/memory_fakes.py`: `FakeEmbeddings(dimension: int = 8)`, `FakeCollection()` with `records`, `last_where`, `fail_on`
- Deferred to Task 7: `LongTermMemory.from_config(...)` and `build_chroma_collection(...)`.

- [ ] **Step 1: Write the shared test doubles**

Create `tests/memory_fakes.py`:

```python
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
```

- [ ] **Step 2: Write the failing long-term memory tests**

Create `tests/test_memory_long_term.py`:

```python
"""Tests for long-term semantic memory behavior against injected fakes."""

from __future__ import annotations

import pytest

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.long_term import LongTermMemory
from deep_research.observability import LangSmithRuntimeConfig, Tracker
from tests.memory_fakes import FakeCollection, FakeEmbeddings


@pytest.fixture
def collection() -> FakeCollection:
    return FakeCollection()


@pytest.fixture
def embeddings() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture
def memory(
    collection: FakeCollection, embeddings: FakeEmbeddings
) -> LongTermMemory:
    return LongTermMemory(collection=collection, embeddings=embeddings)


def _finding(content: str, **overrides: object) -> MemoryEntry:
    payload: dict[str, object] = {
        "entry_type": "finding",
        "content": content,
        "session_id": "session-1",
        "agent_id": "researcher",
        "confidence": 0.9,
    }
    payload.update(overrides)
    return MemoryEntry.model_validate(payload)


@pytest.mark.asyncio
async def test_saved_entries_are_recovered_by_semantic_query(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    entry = _finding("Surface codes reduced logical error rates in 2026.")

    assert await memory.save(entry) is True
    assert collection.count() == 1

    results = await memory.query(entry.content, top_k=3)

    assert len(results) == 1
    assert results[0].entry == entry
    assert results[0].distance == pytest.approx(0.0)
    assert results[0].relevance == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_save_many_writes_every_entry_once(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    entries = [_finding("first finding"), _finding("second finding")]

    assert await memory.save_many(entries) == 2
    assert collection.count() == 2
    assert await memory.save_many([]) == 0


@pytest.mark.asyncio
async def test_saving_the_same_id_twice_updates_in_place(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    await memory.save(_finding("old text", entry_id="entry-1"))
    await memory.save(_finding("new text", entry_id="entry-1"))

    assert collection.count() == 1
    assert collection.records["entry-1"]["document"] == "new text"


@pytest.mark.asyncio
async def test_query_filters_by_entry_type(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    await memory.save(_finding("shared subject matter"))
    await memory.save(
        _finding("shared subject matter", entry_type="report_summary")
    )

    results = await memory.query(
        "shared subject matter", top_k=5, entry_type="report_summary"
    )

    assert collection.last_where == {"entry_type": {"$eq": "report_summary"}}
    assert len(results) == 1
    assert results[0].entry.entry_type == "report_summary"


@pytest.mark.asyncio
async def test_query_combines_entry_type_and_metadata_filters(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    await memory.save(
        _finding("hardware progress", attributes={"sub_topic": "hardware"})
    )
    await memory.save(
        _finding("policy progress", attributes={"sub_topic": "policy"})
    )

    results = await memory.query(
        "progress",
        top_k=5,
        entry_type="finding",
        where={"sub_topic": "hardware"},
    )

    assert collection.last_where == {
        "$and": [
            {"entry_type": {"$eq": "finding"}},
            {"sub_topic": {"$eq": "hardware"}},
        ]
    }
    assert [result.entry.attributes["sub_topic"] for result in results] == [
        "hardware"
    ]


@pytest.mark.asyncio
async def test_query_without_filters_sends_no_where_clause(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    await memory.save(_finding("anything"))

    await memory.query("anything", top_k=1)

    assert collection.last_where is None


@pytest.mark.asyncio
async def test_query_honors_top_k(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    for index in range(5):
        await memory.save(_finding(f"finding number {index}"))

    assert len(await memory.query("finding number 0", top_k=2)) == 2


@pytest.mark.asyncio
async def test_query_rejects_invalid_arguments(memory: LongTermMemory) -> None:
    with pytest.raises(ValueError, match="top_k must be at least 1"):
        await memory.query("anything", top_k=0)
    with pytest.raises(ValueError, match="query text must not be blank"):
        await memory.query("   ")


@pytest.mark.asyncio
async def test_write_failures_are_recoverable_and_recorded(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    collection.fail_on.add("upsert")

    assert await memory.save(_finding("unreachable")) is False

    errors = memory.drain_errors()
    assert len(errors) == 1
    assert errors[0].error_type == "long_term_memory_unavailable"
    assert errors[0].source == "long_term_memory"
    assert errors[0].recoverable is True
    assert errors[0].details["operation"] == "save"
    assert errors[0].details["exception_type"] == "RuntimeError"
    assert memory.errors == ()


@pytest.mark.asyncio
async def test_query_failures_return_no_results_and_record_an_error(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    collection.fail_on.add("query")

    assert await memory.query("anything") == []

    errors = memory.drain_errors()
    assert len(errors) == 1
    assert errors[0].details["operation"] == "query"


@pytest.mark.asyncio
async def test_embedding_failures_are_recoverable(
    memory: LongTermMemory, embeddings: FakeEmbeddings
) -> None:
    embeddings.fail = True

    assert await memory.save(_finding("unreachable")) is False
    assert await memory.query("anything") == []
    assert len(memory.drain_errors()) == 2


@pytest.mark.asyncio
async def test_source_reputation_is_created_then_blended_on_update(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    first = await memory.update_source_reputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=0.9,
        session_id="session-1",
        agent_id="source_evaluator",
        notes="Peer reviewed.",
    )

    assert first is not None
    assert first.observations == 1
    assert first.reputation_score == pytest.approx(0.9)

    second = await memory.update_source_reputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=0.5,
        session_id="session-2",
        agent_id="source_evaluator",
    )

    assert second is not None
    assert second.observations == 2
    assert second.reputation_score == pytest.approx(0.7)
    assert second.notes == "Peer reviewed."
    assert collection.count() == 1

    stored = await memory.get_source_reputation("https://example.org/a")
    assert stored is not None
    assert stored.observations == 2
    assert stored.reputation_score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_unknown_source_reputation_returns_none(
    memory: LongTermMemory,
) -> None:
    assert await memory.get_source_reputation("https://example.org/none") is None
    assert memory.errors == ()


@pytest.mark.asyncio
async def test_source_reputation_read_failure_is_recoverable(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    collection.fail_on.add("get")

    assert await memory.get_source_reputation("https://example.org/a") is None
    assert memory.drain_errors()[0].error_type == "long_term_memory_unavailable"


@pytest.mark.asyncio
async def test_operations_emit_memory_metrics_inside_a_session(
    collection: FakeCollection, embeddings: FakeEmbeddings
) -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    memory = LongTermMemory(
        collection=collection, embeddings=embeddings, tracker=tracker
    )

    async with tracker.session_span("session-1", "Why?"):
        async with tracker.agent_span("researcher"):
            await memory.save(_finding("instrumented finding"))
            await memory.query("instrumented finding", top_k=4)

    metrics = [
        metric for metric in tracker.metrics if metric.metric_type == "memory"
    ]
    assert [metric.operation for metric in metrics] == ["save", "query"]
    assert metrics[0].entry_type == "finding"
    assert metrics[0].result_count == 1
    assert metrics[0].top_k is None
    assert metrics[1].top_k == 4
    assert metrics[1].result_count == 1
    assert all(metric.memory_layer == "long_term" for metric in metrics)
    assert all(metric.agent_name == "researcher" for metric in metrics)


@pytest.mark.asyncio
async def test_failed_operations_are_reported_as_failed_spans(
    collection: FakeCollection, embeddings: FakeEmbeddings
) -> None:
    tracker = Tracker(LangSmithRuntimeConfig(tracing_enabled=False))
    memory = LongTermMemory(
        collection=collection, embeddings=embeddings, tracker=tracker
    )
    collection.fail_on.add("query")

    async with tracker.session_span("session-1", "Why?"):
        assert await memory.query("anything") == []

    metric = next(
        metric for metric in tracker.metrics if metric.metric_type == "memory"
    )
    assert metric.success is False
    assert metric.error_type == "RuntimeError"
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_memory_long_term.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.memory.long_term'`.

- [ ] **Step 4: Implement long-term memory**

Create `src/deep_research/memory/long_term.py`:

```python
"""ChromaDB-backed semantic memory for cross-session research recall.

Every operation is guarded: a backend or embedding failure records a
recoverable ``ResearchError`` and returns an empty result so agents can
continue with short-term state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from deep_research.memory.entries import (
    MemoryEntry,
    MemoryEntryType,
    MemoryQueryResult,
    SourceReputation,
    source_reputation_entry_id,
)
from deep_research.memory.errors import MemoryErrorLog
from deep_research.memory.instrumentation import memory_operation
from deep_research.observability import Tracker
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
            break
        entries.append(
            MemoryEntry.from_storage(
                entry_id=entry_id,
                document=documents[index],
                metadata=dict(metadatas[index]),
            )
        )
    return entries


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
        """Semantic search with optional metadata filters. Never raises."""
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
        """Blend a new judgement into the running reputation and persist it."""
        existing = await self.get_source_reputation(url)
        if existing is None:
            record = SourceReputation(
                url=url,
                title=title,
                reputation_score=reputation_score,
                observations=1,
                notes=notes,
            )
        else:
            observations = existing.observations + 1
            blended = (
                existing.reputation_score * existing.observations + reputation_score
            ) / observations
            record = SourceReputation(
                url=existing.url,
                title=title or existing.title,
                reputation_score=min(1.0, max(0.0, blended)),
                observations=observations,
                notes=notes or existing.notes,
            )
        saved = await self.save(
            record.to_entry(session_id=session_id, agent_id=agent_id)
        )
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
```

- [ ] **Step 5: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_memory_long_term.py -v
ruff check src/deep_research tests
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/deep_research/memory/long_term.py tests/memory_fakes.py tests/test_memory_long_term.py
git commit -m "feat: add long-term semantic memory core"
```

---

### Task 6: OpenAI Embedding Provider

**Files:**
- Modify: `pyproject.toml:8-13`
- Create: `src/deep_research/providers/embeddings.py`
- Modify: `src/deep_research/providers/__init__.py:1`
- Create: `tests/test_providers_embeddings.py`

**Interfaces:**
- Consumes: the `EmbeddingProvider` protocol shape from Task 5 (`embed_query`, `embed_documents`).
- Produces:
  - `DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"`
  - `OpenAIEmbeddingProvider(*, model: str = DEFAULT_EMBEDDING_MODEL, dimensions: int | None = None, client: Any | None = None, api_key: str | None = None, timeout: float = 30.0)`
  - `embed_query(text: str) -> list[float]`, `embed_documents(texts: Sequence[str]) -> list[list[float]]`, `dimension: int | None`
  - `deep_research.providers` exports `DEFAULT_EMBEDDING_MODEL` and `OpenAIEmbeddingProvider`

- [ ] **Step 1: Write the failing embedding provider tests**

Create `tests/test_providers_embeddings.py`:

```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_providers_embeddings.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'deep_research.providers.embeddings'`.

- [ ] **Step 3: Add the OpenAI runtime dependency**

In `pyproject.toml`, replace:

```toml
dependencies = [
    "langsmith>=0.10",
    "pydantic>=2",
    "pyyaml",
]
```

with:

```toml
dependencies = [
    "langsmith>=0.10",
    "openai>=1.40,<3",
    "pydantic>=2",
    "pyyaml",
]
```

Then run:

```bash
pip install -e ".[dev]"
```

- [ ] **Step 4: Implement the provider**

Create `src/deep_research/providers/embeddings.py`:

```python
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
```

- [ ] **Step 5: Export the provider**

Replace `src/deep_research/providers/__init__.py` with:

```python
"""Model and embedding providers. OpenAI only in the first build."""

from deep_research.providers.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "OpenAIEmbeddingProvider",
]
```

- [ ] **Step 6: Run the tests and verify they pass**

Run:

```bash
python -m pytest tests/test_providers_embeddings.py tests/test_imports.py -v
ruff check src/deep_research tests
```

Expected: every test passes and Ruff prints `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/deep_research/providers tests/test_providers_embeddings.py
git commit -m "feat: add OpenAI embedding provider"
```

---

### Task 7: ChromaDB Adapter And Temporary-Directory Integration

**Files:**
- Modify: `pyproject.toml:8-14`
- Modify: `src/deep_research/memory/long_term.py` (append `build_chroma_collection` and `LongTermMemory.from_config`)
- Create: `tests/test_memory_chroma.py`

**Interfaces:**
- Consumes: `VectorCollection`, `EmbeddingProvider`, `LongTermMemory` from Task 5; `MemoryInitializationError` from Task 1; `LongTermMemoryConfig` from `deep_research.utils.config`.
- Produces:
  - `build_chroma_collection(config: LongTermMemoryConfig) -> VectorCollection` — creates `<persist_directory>/chroma/` and returns the named collection; raises `MemoryInitializationError` on any startup failure.
  - `LongTermMemory.from_config(config: LongTermMemoryConfig, *, embeddings: EmbeddingProvider, tracker: Tracker | None = None) -> LongTermMemory`

- [ ] **Step 1: Write the failing ChromaDB integration tests**

Create `tests/test_memory_chroma.py`:

```python
"""Integration tests for the ChromaDB adapter, against a temporary directory."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.errors import MemoryInitializationError
from deep_research.memory.long_term import LongTermMemory, build_chroma_collection
from deep_research.utils.config import LongTermMemoryConfig
from tests.memory_fakes import FakeEmbeddings

pytest.importorskip(
    "chromadb",
    reason="chromadb is a declared runtime dependency; install it with "
    'pip install -e ".[dev]"',
)


def _config(tmp_path: Path, name: str = "test_memory") -> LongTermMemoryConfig:
    return LongTermMemoryConfig(
        collection_name=name, persist_directory=str(tmp_path / "memory")
    )


def _finding(content: str, **overrides: object) -> MemoryEntry:
    payload: dict[str, object] = {
        "entry_type": "finding",
        "content": content,
        "session_id": "session-1",
        "agent_id": "researcher",
        "confidence": 0.9,
    }
    payload.update(overrides)
    return MemoryEntry.model_validate(payload)


@pytest.mark.asyncio
async def test_entries_round_trip_through_a_temporary_chroma_directory(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    memory = LongTermMemory.from_config(config, embeddings=FakeEmbeddings())

    assert await memory.save(_finding("Surface codes improved in 2026.")) is True

    results = await memory.query("Surface codes improved in 2026.", top_k=3)

    assert (tmp_path / "memory" / "chroma").is_dir()
    assert len(results) == 1
    assert results[0].entry.content == "Surface codes improved in 2026."
    assert results[0].entry.session_id == "session-1"
    assert results[0].entry.confidence == pytest.approx(0.9)
    assert memory.errors == ()


@pytest.mark.asyncio
async def test_metadata_filters_reach_the_real_backend(tmp_path: Path) -> None:
    memory = LongTermMemory.from_config(
        _config(tmp_path), embeddings=FakeEmbeddings()
    )
    await memory.save(
        _finding("shared text", attributes={"sub_topic": "hardware"})
    )
    await memory.save(
        _finding("shared text", entry_type="report_summary")
    )

    typed = await memory.query("shared text", top_k=5, entry_type="finding")
    scoped = await memory.query(
        "shared text", top_k=5, entry_type="finding", where={"sub_topic": "hardware"}
    )

    assert [result.entry.entry_type for result in typed] == ["finding"]
    assert len(scoped) == 1
    assert scoped[0].entry.attributes["sub_topic"] == "hardware"


@pytest.mark.asyncio
async def test_source_reputation_updates_persist_across_instances(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    first = LongTermMemory.from_config(config, embeddings=FakeEmbeddings())
    await first.update_source_reputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=1.0,
        session_id="session-1",
        agent_id="source_evaluator",
    )

    second = LongTermMemory.from_config(config, embeddings=FakeEmbeddings())
    updated = await second.update_source_reputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=0.0,
        session_id="session-2",
        agent_id="source_evaluator",
    )

    assert updated is not None
    assert updated.observations == 2
    assert updated.reputation_score == pytest.approx(0.5)


def test_unusable_persist_directory_fails_startup(tmp_path: Path) -> None:
    blocker = tmp_path / "memory"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(MemoryInitializationError):
        build_chroma_collection(_config(tmp_path))


def test_importing_the_memory_package_does_not_import_chromadb() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import deep_research.memory; "
            "assert 'chromadb' not in sys.modules, 'chromadb imported eagerly'",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/test_memory_chroma.py -v
```

Expected: collection error — `ImportError: cannot import name 'build_chroma_collection'`.

- [ ] **Step 3: Add the ChromaDB runtime dependency**

In `pyproject.toml`, replace:

```toml
dependencies = [
    "langsmith>=0.10",
    "openai>=1.40,<3",
    "pydantic>=2",
    "pyyaml",
]
```

with:

```toml
dependencies = [
    "chromadb>=0.5,<2",
    "langsmith>=0.10",
    "openai>=1.40,<3",
    "pydantic>=2",
    "pyyaml",
]
```

Then run:

```bash
pip install -e ".[dev]"
python -c "import chromadb; print(chromadb.__version__)"
```

Expected: the install completes and a version is printed. This is a large install; allow several minutes.

- [ ] **Step 4: Implement the ChromaDB adapter**

In `src/deep_research/memory/long_term.py`, extend the import block. Insert `from pathlib import Path` between the `collections.abc` and `typing` imports so the stdlib group stays isort-clean:

```python
import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
```

Replace the errors import:

```python
from deep_research.memory.errors import MemoryErrorLog, MemoryInitializationError
```

Add a config import between the `deep_research.observability` and `deep_research.utils.types` imports:

```python
from deep_research.utils.config import LongTermMemoryConfig
```

Add this function immediately before the `LongTermMemory` class:

```python
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
```

Add this classmethod to `LongTermMemory`, directly after `__init__`:

```python
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
```

`embedding_function=None` is explicit because ChromaDB otherwise instantiates its default ONNX embedding function on collection creation, which downloads a model. Every vector is supplied by the injected `EmbeddingProvider`, so the collection must never compute one itself.

- [ ] **Step 5: Run the ChromaDB tests and verify they pass**

Run:

```bash
python -m pytest tests/test_memory_chroma.py -v
```

Expected: all five tests report `PASSED`.

- [ ] **Step 6: Confirm the adapter matched the installed ChromaDB version**

Run:

```bash
python -m pytest tests/test_memory_chroma.py -v -rs
```

Expected: no `SKIPPED` lines. A skip means `chromadb` did not install and Step 3 must be resolved before this task is complete. If any test failed on an API mismatch, apply the fallbacks described in "Known Risks And Unknowns" item 2 and note the change in the commit message.

- [ ] **Step 7: Confirm no test wrote to the runtime memory directory**

Run:

```bash
git status --short
ls memory 2>/dev/null || echo "no runtime memory directory"
```

Expected: only intended files are modified, and no repository-level `memory/` directory exists.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/deep_research/memory/long_term.py tests/test_memory_chroma.py
git commit -m "feat: add ChromaDB long-term memory adapter"
```

---

### Task 8: Public Exports, Documentation, And Full Verification

**Files:**
- Modify: `src/deep_research/memory/__init__.py:1`
- Modify: `tests/test_imports.py` (append)
- Modify: `README.md:8` (Project Status), and add a Memory section after Observability; update the Phases list

**Interfaces:**
- Consumes: every public contract built in Tasks 1-7.
- Produces: the supported `deep_research.memory` import surface and user-facing memory documentation.

- [ ] **Step 1: Write the failing public import test**

Append to `tests/test_imports.py`:

```python
def test_memory_contracts_import_from_package() -> None:
    from deep_research.memory import (  # noqa: F401
        EmbeddingProvider,
        LongTermMemory,
        MemoryEntry,
        MemoryEntryType,
        MemoryErrorLog,
        MemoryInitializationError,
        MemoryQueryResult,
        MemoryStackError,
        ProceduralMemory,
        ScratchpadEntry,
        ScratchpadMemory,
        SourceReputation,
        StrategyRecord,
        Summarizer,
        VectorCollection,
        build_chroma_collection,
        memory_operation,
        source_reputation_entry_id,
    )


def test_provider_contracts_import_from_package() -> None:
    from deep_research.providers import (  # noqa: F401
        DEFAULT_EMBEDDING_MODEL,
        OpenAIEmbeddingProvider,
    )
```

- [ ] **Step 2: Run the import tests and verify they fail**

Run:

```bash
python -m pytest tests/test_imports.py -v
```

Expected: `test_memory_contracts_import_from_package` fails with `ImportError: cannot import name 'LongTermMemory' from 'deep_research.memory'`.

- [ ] **Step 3: Export the memory API**

Replace `src/deep_research/memory/__init__.py` with:

```python
"""Short-term, long-term, and procedural agent memory."""

from deep_research.memory.entries import (
    MemoryEntry,
    MemoryEntryType,
    MemoryQueryResult,
    MetadataValue,
    ScratchpadEntry,
    ScratchpadEntryKind,
    SourceReputation,
    StrategyRecord,
    source_reputation_entry_id,
)
from deep_research.memory.errors import (
    MemoryErrorLog,
    MemoryInitializationError,
    MemoryStackError,
)
from deep_research.memory.instrumentation import (
    MemoryOperationHandle,
    memory_operation,
)
from deep_research.memory.long_term import (
    DEFAULT_TOP_K,
    EmbeddingProvider,
    LongTermMemory,
    VectorCollection,
    build_chroma_collection,
)
from deep_research.memory.procedural import ProceduralMemory
from deep_research.memory.scratchpad import ScratchpadMemory, Summarizer

__all__ = [
    "DEFAULT_TOP_K",
    "EmbeddingProvider",
    "LongTermMemory",
    "MemoryEntry",
    "MemoryEntryType",
    "MemoryErrorLog",
    "MemoryInitializationError",
    "MemoryOperationHandle",
    "MemoryQueryResult",
    "MemoryStackError",
    "MetadataValue",
    "ProceduralMemory",
    "ScratchpadEntry",
    "ScratchpadEntryKind",
    "ScratchpadMemory",
    "SourceReputation",
    "StrategyRecord",
    "Summarizer",
    "VectorCollection",
    "build_chroma_collection",
    "memory_operation",
    "source_reputation_entry_id",
]
```

- [ ] **Step 4: Run the import tests and verify they pass**

Run:

```bash
python -m pytest tests/test_imports.py -v
```

Expected: all import tests pass.

- [ ] **Step 5: Document the memory stack**

In `README.md`, replace the Project Status line:

```markdown
Foundation phase — package skeleton, typed configuration/state, and the LangSmith observability foundation.
```

with:

```markdown
Foundation phase — package skeleton, typed configuration/state, the LangSmith observability foundation, and the three-layer memory stack.
```

Add this section after the Observability section and before `## Development`. The outer fence below is four backticks so the nested Python block survives copy-paste; write only the inner content into `README.md`:

````markdown
## Memory

Three layers, each independently usable:

- `ScratchpadMemory` — synchronous, bounded, per-agent and per-session. Never
  persisted. Optional summarization hook compacts the window instead of
  silently dropping the oldest notes.
- `LongTermMemory` — async, ChromaDB-backed semantic recall over verified
  findings, source reputations, report summaries, and notable failed
  strategies. Persists under `<memory.long_term.persist_directory>/chroma/`.
- `ProceduralMemory` — async, JSON-backed strategy registry at
  `memory/strategies.json`.

```python
from deep_research.memory import LongTermMemory, ProceduralMemory, ScratchpadMemory
from deep_research.providers import OpenAIEmbeddingProvider
from deep_research.utils.config import load_config
from deep_research.utils.types import merge_research_state

settings = load_config("config.yaml")

pad = ScratchpadMemory.from_config(
    settings.memory.short_term, session_id="session-123", agent_name="researcher"
)
pad.add("Tavily returned 5 results.", kind="observation")

long_term = LongTermMemory.from_config(
    settings.memory.long_term, embeddings=OpenAIEmbeddingProvider()
)
hits = await long_term.query("quantum error correction", top_k=5)

procedural = ProceduralMemory.from_config(settings.memory.procedural)
await procedural.load()
await procedural.record_session_outcome(
    topic_type="technology", succeeded=True, iterations=3
)

state = merge_research_state(state, {"errors": long_term.drain_errors()})
```

Memory failures are recoverable. A long-term write returns `False`, a query
returns `[]`, and each failure appends a recoverable `ResearchError` that
`drain_errors()` hands back for merging into `ResearchState.errors` — agents
continue with short-term state. Only startup problems raise
`MemoryInitializationError`. A corrupt `memory/strategies.json` is renamed to
`memory/strategies.json.corrupt-<timestamp>.bak` and the registry restarts
empty.

Long-term and procedural operations emit a `MemoryMetric` (operation, layer,
entry type, top-k, result count, latency, error type) whenever a tracker and an
active session span are available.
````

In the Phases list, replace:

```markdown
- Phase 1: Core package foundation, config, types, providers ← current
- Phase 2: Memory and tools
```

with:

```markdown
- Phase 1: Core package foundation, config, types, providers
- Phase 2: Memory and tools ← current (memory complete, tools pending)
```

- [ ] **Step 6: Run the full verification suite**

Run:

```bash
python -m pytest -v
ruff check src tests
git diff --check
```

Expected: the complete suite passes with no skips, Ruff prints `All checks passed!`, and `git diff --check` prints no output.

- [ ] **Step 7: Verify every acceptance criterion**

Run:

```bash
python -m pytest tests/test_memory_chroma.py tests/test_memory_long_term.py -q
python -m pytest tests/test_memory_procedural.py -q
git status --short
ls memory output 2>/dev/null || echo "no runtime directories created"
```

Expected: long-term save/query tests pass against both fakes and real ChromaDB (criterion 1); procedural persistence tests pass (criterion 2); no runtime `memory/` or `output/` directory exists and `git status` shows no stray files (criterion 3). Criterion 4 is covered by the `ResearchError` assertions in `tests/test_memory_scratchpad.py`, `tests/test_memory_procedural.py`, and `tests/test_memory_long_term.py`.

- [ ] **Step 8: Commit**

```bash
git add src/deep_research/memory/__init__.py tests/test_imports.py README.md
git commit -m "docs: publish memory stack API"
```

- [ ] **Step 9: Verify the branch is clean**

Run:

```bash
git status --short
```

Expected: no output.

---

## Spec Coverage Map

| Spec requirement | Task |
| --- | --- |
| `ScratchpadMemory` (per-agent/session, bounded, not persisted, recent retrieval, summarization hook) | 2 |
| `LongTermMemory` (ChromaDB, OpenAI embeddings, semantic query with metadata filters) | 5, 6, 7 |
| `ProceduralMemory` (JSON registry, topic type, query templates, trusted source patterns, success rate, notes) | 4 |
| Memory entry models | 1 |
| ChromaDB integration | 7 |
| OpenAI embedding integration | 6 |
| Observability: operation, entry type, top-k, result count, latency, storage errors | 3 (schema/span), 4 and 5 (emission) |
| Recoverable failures except startup init | 1 (`MemoryInitializationError`), 2, 4, 5 |
| Long-term unavailable → continue on short-term state + record `ResearchError` | 5 |
| Corrupt procedural JSON → backup suffix + empty registry | 4 |
| Test: scratchpad bounds and retrieval | 2 |
| Test: ChromaDB save/query in a temporary directory | 7 |
| Test: metadata filtering | 5 (fakes), 7 (real backend) |
| Test: source reputation save/update | 5, 7 |
| Test: procedural strategy load/save/update | 4 |
| Test: corrupt procedural memory fallback | 4 |
| AC: agents can save and query long-term memory | 5, 7, 8 |
| AC: procedural memory persists strategy outcomes | 4 |
| AC: memory tests do not depend on production runtime directories | 4 Step 7, 7 Step 7, 8 Step 7 |
| AC: memory failures are represented as structured errors | 1, 2, 4, 5 |
