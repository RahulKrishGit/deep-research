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
async def test_update_does_not_clobber_history_when_the_read_fails(
    memory: LongTermMemory, collection: FakeCollection
) -> None:
    for _ in range(4):
        await memory.update_source_reputation(
            url="https://example.org/a",
            title="Example",
            reputation_score=1.0,
            session_id="session-1",
            agent_id="source_evaluator",
        )
    memory.drain_errors()

    collection.fail_on.add("get")
    result = await memory.update_source_reputation(
        url="https://example.org/a",
        title="Example",
        reputation_score=0.0,
        session_id="session-2",
        agent_id="source_evaluator",
    )

    assert result is None
    errors = memory.drain_errors()
    assert len(errors) == 1
    assert errors[0].error_type == "long_term_memory_unavailable"

    collection.fail_on.discard("get")
    stored = await memory.get_source_reputation("https://example.org/a")
    assert stored is not None
    assert stored.observations == 4
    assert stored.reputation_score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_out_of_range_score_and_blank_title_are_normalized_consistently(
    memory: LongTermMemory,
) -> None:
    created = await memory.update_source_reputation(
        url="https://example.org/b",
        title="",
        reputation_score=1.5,
        session_id="session-1",
        agent_id="source_evaluator",
    )

    assert created is not None
    assert created.reputation_score == pytest.approx(1.0)
    assert created.title.strip() != ""

    updated = await memory.update_source_reputation(
        url="https://example.org/b",
        title="",
        reputation_score=-3.0,
        session_id="session-2",
        agent_id="source_evaluator",
    )

    assert updated is not None
    # The prior observation clamped to 1.0 and the new one clamps to 0.0,
    # so the blended running average is 0.5 -- clamping happens before
    # blending, identically to the create path, not after.
    assert updated.reputation_score == pytest.approx(0.5)
    assert updated.title.strip() != ""
    assert memory.errors == ()


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
