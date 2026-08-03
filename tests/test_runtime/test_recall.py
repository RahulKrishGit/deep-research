"""Tests for the memory snapshot one research session starts from."""

from __future__ import annotations

import pytest

from deep_research.memory.entries import MemoryEntry, SourceReputation
from deep_research.memory.long_term import LongTermMemory
from deep_research.memory.procedural import ProceduralMemory
from deep_research.runtime.recall import (
    RECALLED_SUB_TOPIC,
    recall_memory_context,
)
from deep_research.utils.types import MemorySnapshot
from tests.memory_fakes import FakeCollection, FakeEmbeddings

QUESTION = "How mature is quantum error correction?"


def build_memory() -> LongTermMemory:
    return LongTermMemory(collection=FakeCollection(), embeddings=FakeEmbeddings())


@pytest.mark.asyncio
async def test_recall_returns_an_empty_snapshot_without_memory() -> None:
    snapshot = await recall_memory_context(question=QUESTION, long_term=None)

    assert snapshot == MemorySnapshot()


@pytest.mark.asyncio
async def test_recall_turns_stored_findings_into_findings() -> None:
    memory = build_memory()
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

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert len(snapshot.similar_findings) == 1
    finding = snapshot.similar_findings[0]
    assert finding.content == "Break-even was reached in 2025."
    assert finding.source_url == "https://example.org/a"
    assert finding.related_sub_topic == "Error correction"
    assert finding.confidence == 0.8


@pytest.mark.asyncio
async def test_a_finding_without_a_sub_topic_gets_the_recall_placeholder() -> None:
    memory = build_memory()
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="Break-even was reached in 2025.",
            session_id="session-0",
            agent_id="researcher",
            source_url="https://example.org/a",
            source_title="QEC 2025",
        )
    )

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot.similar_findings[0].related_sub_topic == RECALLED_SUB_TOPIC


@pytest.mark.asyncio
async def test_a_finding_with_no_source_is_skipped_rather_than_faked() -> None:
    memory = build_memory()
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="An unattributed note.",
            session_id="session-0",
            agent_id="researcher",
        )
    )

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot.similar_findings == []


@pytest.mark.asyncio
async def test_recall_collects_reputations_for_the_recalled_sources() -> None:
    memory = build_memory()
    await memory.save(
        MemoryEntry(
            entry_type="finding",
            content="Break-even was reached in 2025.",
            session_id="session-0",
            agent_id="researcher",
            source_url="https://example.org/a",
            source_title="QEC 2025",
        )
    )
    await memory.save(
        SourceReputation(
            url="https://example.org/a",
            title="QEC 2025",
            reputation_score=0.75,
        ).to_entry(session_id="session-0", agent_id="source_evaluator")
    )

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot.known_source_reputations == {"https://example.org/a": 0.75}


@pytest.mark.asyncio
async def test_recall_reads_query_templates_out_of_procedural_memory(
    tmp_path,
) -> None:
    procedural = ProceduralMemory(tmp_path / "strategies.json")
    await procedural.load()
    await procedural.record_session_outcome(
        topic_type="technology",
        succeeded=True,
        iterations=2,
        query_templates=["{topic} 2025 benchmark", "{topic} limitations"],
    )

    snapshot = await recall_memory_context(
        question=QUESTION, long_term=None, procedural=procedural
    )

    assert snapshot.suggested_strategies == [
        "{topic} 2025 benchmark",
        "{topic} limitations",
    ]


@pytest.mark.asyncio
async def test_recall_survives_a_dead_backend() -> None:
    collection = FakeCollection()
    collection.fail_on.add("query")
    memory = LongTermMemory(collection=collection, embeddings=FakeEmbeddings())

    snapshot = await recall_memory_context(question=QUESTION, long_term=memory)

    assert snapshot == MemorySnapshot()
