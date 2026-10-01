"""Tests for ``gather_or_cancel`` (latency plan, Task 7)."""

from __future__ import annotations

import asyncio

import pytest

from deep_research.utils.concurrency import gather_or_cancel


@pytest.mark.asyncio
async def test_results_come_back_in_argument_order_whatever_finishes_first() -> None:
    async def after(seconds: float, value: str) -> str:
        await asyncio.sleep(seconds)
        return value

    assert await gather_or_cancel(after(0.05, "first"), after(0.0, "second")) == [
        "first",
        "second",
    ]


@pytest.mark.asyncio
async def test_the_first_failure_cancels_the_rest_and_propagates_as_itself() -> None:
    """Never an ``ExceptionGroup``, and nothing left running afterwards."""
    cancelled: list[str] = []

    async def slow() -> str:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append("slow")
            raise
        return "slow"

    async def failing() -> str:
        await asyncio.sleep(0)
        raise LookupError("the second failed")

    with pytest.raises(LookupError, match="the second failed"):
        await gather_or_cancel(slow(), failing())

    assert cancelled == ["slow"]


@pytest.mark.asyncio
async def test_cancelling_the_caller_cancels_every_awaitable() -> None:
    cancelled: list[int] = []

    async def wait(index: int) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(index)
            raise

    caller = asyncio.create_task(gather_or_cancel(wait(1), wait(2)))
    await asyncio.sleep(0.01)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert sorted(cancelled) == [1, 2]
