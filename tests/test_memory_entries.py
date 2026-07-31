"""Tests for shared memory record models and error contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from deep_research.memory.entries import (
    MemoryEntry,
    MemoryQueryResult,
    ScratchpadEntry,
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
    assert all(
        isinstance(value, (str, int, float, bool)) for value in metadata.values()
    )

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


def test_memory_entry_rejects_non_finite_attribute_values() -> None:
    with pytest.raises(ValidationError):
        _entry(attributes={"score": float("nan")})
    with pytest.raises(ValidationError):
        _entry(attributes={"score": float("inf")})


def test_memory_entry_from_storage_raises_validation_error_on_missing_fields() -> None:
    with pytest.raises(ValidationError):
        MemoryEntry.from_storage(
            entry_id="entry-1",
            document="content",
            metadata={"entry_type": "finding", "session_id": "session-1"},
        )


def test_memory_query_result_derives_relevance_from_distance() -> None:
    assert MemoryQueryResult(entry=_entry(), distance=0.0).relevance == 1.0
    assert MemoryQueryResult(entry=_entry(), distance=1.0).relevance == 0.5
    assert MemoryQueryResult(entry=_entry(), distance=None).relevance == 0.0


def test_memory_query_result_rejects_non_finite_distance() -> None:
    with pytest.raises(ValidationError):
        MemoryQueryResult(entry=_entry(), distance=float("inf"))
    with pytest.raises(ValidationError):
        MemoryQueryResult(entry=_entry(), distance=float("nan"))


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


def test_scratchpad_entry_defaults_and_construction() -> None:
    entry = ScratchpadEntry(agent_name="researcher", content="Trying query X.")

    assert entry.kind == "observation"
    assert entry.metadata == {}
    assert len(entry.timestamp) > 0


def test_scratchpad_entry_accepts_each_declared_kind() -> None:
    for kind in ("thought", "observation", "decision", "summary"):
        entry = ScratchpadEntry(agent_name="researcher", kind=kind, content="note")
        assert entry.kind == kind


def test_scratchpad_entry_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        ScratchpadEntry(agent_name="researcher", kind="musing", content="note")


def test_scratchpad_entry_rejects_blank_agent_name() -> None:
    with pytest.raises(ValidationError):
        ScratchpadEntry(agent_name="", content="note")


def test_scratchpad_entry_rejects_non_finite_metadata_values() -> None:
    with pytest.raises(ValidationError):
        ScratchpadEntry(
            agent_name="researcher",
            content="note",
            metadata={"score": float("nan")},
        )
    with pytest.raises(ValidationError):
        ScratchpadEntry(
            agent_name="researcher",
            content="note",
            metadata={"nested": {"score": float("inf")}},
        )


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
