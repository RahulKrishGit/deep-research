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
