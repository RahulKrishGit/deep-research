"""Tests for the JSON-backed procedural strategy registry."""

from __future__ import annotations

import asyncio
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
async def test_non_utf8_registry_is_backed_up_and_restarts_empty(
    strategies_path: Path,
) -> None:
    strategies_path.parent.mkdir(parents=True, exist_ok=True)
    strategies_path.write_bytes(b"\xff\xfe\x00\x01garbage")
    memory = ProceduralMemory(strategies_path)

    await memory.load()

    backups = list(strategies_path.parent.glob("strategies.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"\xff\xfe\x00\x01garbage"
    assert not strategies_path.exists()
    assert memory.strategies == ()

    errors = memory.drain_errors()
    assert len(errors) == 1
    assert errors[0].error_type == "procedural_memory_corrupt"
    assert errors[0].recoverable is True


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

    def explode(self: Path) -> bytes:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_bytes", explode)
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
async def test_concurrent_record_session_outcome_calls_do_not_race(
    strategies_path: Path,
) -> None:
    memory = ProceduralMemory(strategies_path)
    await memory.load()

    await asyncio.gather(
        *(
            memory.record_session_outcome(
                topic_type="technology", succeeded=True, iterations=1
            )
            for _ in range(40)
        )
    )

    errors = memory.drain_errors()
    assert errors == []
    record = memory.get("technology")
    assert record is not None
    assert record.sessions == 40

    payload = json.loads(strategies_path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["sessions"] == 40


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
