from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest

from deep_research.observability import ToolMetric
from deep_research.observability.tracker import SpanHandle
from deep_research.tools.memory_tools import QueryMemoryTool, SaveToMemoryTool


class FakeMemory:
    def __init__(
        self,
        *,
        entry_id: str = "entry-123",
        matches: Sequence[Mapping[str, object]] = (),
        error: Exception | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.matches = matches
        self.error = error
        self.save_calls: list[dict[str, object]] = []
        self.query_calls: list[dict[str, object]] = []

    async def save(self, content: str, metadata: Mapping[str, object]) -> str:
        self.save_calls.append({"content": content, "metadata": metadata})
        if self.error is not None:
            raise self.error
        return self.entry_id

    async def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, object] | None = None,
    ) -> Sequence[Mapping[str, object]]:
        self.query_calls.append(
            {"query": query, "top_k": top_k, "filters": filters}
        )
        if self.error is not None:
            raise self.error
        return self.matches


class RecordingTracker:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.span = SpanHandle(context=None)  # type: ignore[arg-type]

    @asynccontextmanager
    async def tool_span(self, name: str, inputs: dict[str, object]):
        self.calls.append({"name": name, "inputs": inputs})
        yield self.span


@pytest.mark.asyncio
async def test_save_persists_entry_with_private_summary_and_span_inputs(tracker) -> None:
    memory = FakeMemory()
    tool = SaveToMemoryTool(tracker, memory=memory)
    metadata = {
        "finding_type": "verified",
        "session_id": "session-1",
        "confidence": 0.98,
    }

    async with tracker.session_span("session-1", "question"):
        result = await tool.execute(content="Verified finding", metadata=metadata)

    assert memory.save_calls == [
        {"content": "Verified finding", "metadata": metadata}
    ]
    assert result.success is True
    assert result.data == {"entry_id": "entry-123"}
    assert result.metadata == {"retry_count": 0}
    metric = next(metric for metric in tracker.metrics if isinstance(metric, ToolMetric))
    assert metric.success is True


@pytest.mark.asyncio
async def test_save_records_length_and_metadata_without_exposing_content() -> None:
    memory = FakeMemory()
    tracker = RecordingTracker()
    tool = SaveToMemoryTool(tracker, memory=memory)  # type: ignore[arg-type]

    result = await tool.execute(content="Verified finding", metadata={"kind": "finding"})

    assert result.success is True
    assert tracker.calls == [
        {
            "name": "save_to_memory",
            "inputs": {
                "content_chars": 16,
                "metadata": {"kind": "finding"},
            },
        }
    ]
    assert tracker.span.outputs == {"entry_id": "entry-123", "success": True}
    assert "Verified finding" not in str(tracker.span.outputs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "metadata"),
    [("   ", {}), ("finding", {"bad": object()})],
)
async def test_save_rejects_blank_content_and_non_json_metadata(
    tracker, content: str, metadata: Mapping[str, object]
) -> None:
    memory = FakeMemory()

    async with tracker.session_span("session-1", "question"):
        result = await SaveToMemoryTool(tracker, memory=memory).execute(
            content=content, metadata=metadata
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "ValidationError"
    assert memory.save_calls == []


@pytest.mark.asyncio
async def test_query_preserves_order_and_passes_configuration(tracker) -> None:
    matches = (
        {"entry_id": "entry-1", "content": "first", "score": 0.9},
        {"entry_id": "entry-2", "content": "second", "score": 0.8},
    )
    memory = FakeMemory(matches=matches)
    filters = {"session_id": "session-1"}

    async with tracker.session_span("session-1", "question"):
        result = await QueryMemoryTool(tracker, memory=memory).execute(
            query="verified finding", top_k=3, filters=filters
        )

    assert memory.query_calls == [
        {"query": "verified finding", "top_k": 3, "filters": filters}
    ]
    assert result.success is True
    assert result.data == {"matches": list(matches)}
    assert result.metadata == {"result_count": 2, "retry_count": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "top_k", "filters"),
    [("   ", 3, None), ("query", 0, None), ("query", 3, {"bad": object()})],
)
async def test_query_rejects_invalid_arguments_without_calling_backend(
    tracker, query: str, top_k: int, filters: Mapping[str, object] | None
) -> None:
    memory = FakeMemory()

    async with tracker.session_span("session-1", "question"):
        result = await QueryMemoryTool(tracker, memory=memory).execute(
            query=query, top_k=top_k, filters=filters
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "ValidationError"
    assert memory.query_calls == []


@pytest.mark.asyncio
async def test_query_backend_failure_is_structured_and_marks_metric_failed(tracker) -> None:
    memory = FakeMemory(error=RuntimeError("memory unavailable"))

    async with tracker.session_span("session-1", "question"):
        result = await QueryMemoryTool(tracker, memory=memory).execute(query="topic")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == "RuntimeError"
    metric = next(metric for metric in tracker.metrics if isinstance(metric, ToolMetric))
    assert metric.success is False
    assert metric.error_type == "RuntimeError"
