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
