"""Tests for the long-term-memory-to-tool-protocol bridge."""

from __future__ import annotations

import pytest

from deep_research.memory.entries import MemoryEntry
from deep_research.memory.long_term import LongTermMemory
from deep_research.runtime.memory_bridge import (
    DEFAULT_BRIDGE_AGENT_ID,
    DEFAULT_BRIDGE_ENTRY_TYPE,
    LongTermMemoryBridge,
)
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool
from tests.memory_fakes import FakeCollection, FakeEmbeddings


def build_memory() -> tuple[LongTermMemory, FakeCollection]:
    collection = FakeCollection()
    return (
        LongTermMemory(collection=collection, embeddings=FakeEmbeddings()),
        collection,
    )


@pytest.mark.asyncio
async def test_save_stores_a_finding_and_returns_its_entry_id() -> None:
    memory, collection = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    entry_id = await bridge.save(
        "Break-even was reached in 2025.",
        {
            "entry_type": "finding",
            "agent_id": "synthesizer",
            "confidence": 0.9,
            "source_url": "https://example.org/a",
            "verdict": "verified",
        },
    )

    assert entry_id in collection.records
    stored = collection.records[entry_id]
    assert stored["document"] == "Break-even was reached in 2025."
    assert stored["metadata"]["session_id"] == "session-1"
    assert stored["metadata"]["agent_id"] == "synthesizer"
    assert stored["metadata"]["verdict"] == "verified"


@pytest.mark.asyncio
async def test_save_fills_in_the_defaults_a_model_forgot() -> None:
    memory, collection = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    entry_id = await bridge.save("A bare finding.", {})

    metadata = collection.records[entry_id]["metadata"]
    assert metadata["entry_type"] == DEFAULT_BRIDGE_ENTRY_TYPE
    assert metadata["agent_id"] == DEFAULT_BRIDGE_AGENT_ID
    assert metadata["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_save_drops_metadata_a_vector_store_cannot_hold() -> None:
    memory, collection = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    entry_id = await bridge.save(
        "A finding.",
        {"nested": {"a": 1}, "listed": [1, 2], "missing": None, "kept": "yes"},
    )

    metadata = collection.records[entry_id]["metadata"]
    assert metadata["kept"] == "yes"
    assert "nested" not in metadata
    assert "listed" not in metadata
    assert "missing" not in metadata


@pytest.mark.asyncio
async def test_save_raises_when_the_backend_rejects_the_write() -> None:
    memory, collection = build_memory()
    collection.fail_on.add("upsert")
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    with pytest.raises(RuntimeError, match="long-term memory"):
        await bridge.save("A finding.", {})


@pytest.mark.asyncio
async def test_query_returns_json_safe_mappings() -> None:
    memory, _ = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="Break-even was reached in 2025.",
            session_id="session-0",
            agent_id="researcher",
            confidence=0.8,
            source_url="https://example.org/a",
            source_title="QEC 2025",
            attributes={"related_sub_topic": "Error correction"},
        )
    )

    matches = await bridge.query("break-even", top_k=3)

    assert len(matches) == 1
    match = matches[0]
    assert match["content"] == "Break-even was reached in 2025."
    assert match["entry_type"] == "finding"
    assert match["source_url"] == "https://example.org/a"
    assert match["source_title"] == "QEC 2025"
    assert match["agent_id"] == "researcher"
    assert match["attributes"] == {"related_sub_topic": "Error correction"}
    assert 0.0 <= float(match["relevance"]) <= 1.0


@pytest.mark.asyncio
async def test_query_rejects_a_non_string_entry_type_filter() -> None:
    """A malformed filter is a tool failure, not a silently broadened query."""
    memory, _ = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    with pytest.raises(ValueError, match="entry_type"):
        await bridge.query("error correction", filters={"entry_type": 42})


@pytest.mark.asyncio
async def test_query_filters_by_entry_type() -> None:
    memory, _ = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")
    for entry_type, content in (
        ("finding", "A finding about error correction."),
        ("report_summary", "A summary about error correction."),
    ):
        await memory.save(
            MemoryEntry(
                entry_type=entry_type,
                content=content,
                session_id="session-0",
                agent_id="researcher",
            )
        )

    matches = await bridge.query(
        "error correction", top_k=5, filters={"entry_type": "report_summary"}
    )

    assert [match["entry_type"] for match in matches] == ["report_summary"]


@pytest.mark.asyncio
async def test_the_bridge_satisfies_the_memory_tools(tracker) -> None:
    """The bridge is accepted by the real tools, not just by a protocol."""
    memory, _ = build_memory()
    bridge = LongTermMemoryBridge(memory, session_id="session-1")

    async with tracker.session_span("session-1", "a question"):
        saved = await SaveToMemoryTool(tracker, bridge).execute(
            content="Break-even was reached in 2025.",
            metadata={"entry_type": "finding", "agent_id": "researcher"},
        )
        queried = await QueryMemoryTool(tracker, bridge).execute(
            query="break-even", top_k=3
        )

    assert saved.success, saved.error
    assert queried.success, queried.error
    assert queried.data["matches"][0]["content"] == (
        "Break-even was reached in 2025."
    )
