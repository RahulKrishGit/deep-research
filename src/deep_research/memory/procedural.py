"""JSON-backed procedural memory: which research strategies actually worked."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
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
from deep_research.utils.types import ResearchError, _utc_now_iso

_STRATEGY_LIST_ADAPTER = TypeAdapter(list[StrategyRecord])
_MAX_NOTES = 50


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
    """Write JSON through a unique temporary file so a crash cannot truncate the
    registry and concurrent writers never collide on the same temp path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


class ProceduralMemory:
    """A strategy registry keyed by topic type, persisted as a JSON list."""

    def __init__(self, path: Path | str, *, tracker: Tracker | None = None) -> None:
        self._path = Path(path)
        self._tracker = tracker
        self._strategies: dict[str, StrategyRecord] = {}
        self._error_log = MemoryErrorLog("procedural_memory")
        self._loaded = False
        self._lock = asyncio.Lock()

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
        async with self._lock:
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

        # The read-modify-write of the in-memory registry and its persisted
        # write must be serialized per instance, or concurrent callers can
        # interleave on the same topic_type and lose updates.
        async with self._lock:
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
            raw_bytes = self._path.read_bytes()
        except OSError as error:
            raise MemoryInitializationError(
                f"cannot read procedural memory at {self._path}: "
                f"{type(error).__name__}"
            ) from error
        try:
            raw = raw_bytes.decode("utf-8")
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
