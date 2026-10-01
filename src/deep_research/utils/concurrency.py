"""Run a few awaitables together without leaving any of them running.

``asyncio.gather`` propagates the first failure at once and leaves every
sibling running, and ``asyncio.TaskGroup`` wraps failures in an
``ExceptionGroup`` that no caller here catches. The latency work (audit O3,
O10) runs two or three model calls together where they used to run one after
another, so it needs the one-after-another failure shape: the first failure
propagates as itself, and nothing it started keeps calling a provider.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


async def gather_or_cancel(*awaitables: Awaitable[T]) -> list[T]:
    """Await ``awaitables`` together; return their results in argument order.

    On the first failure -- an exception from any of them, or this call being
    cancelled -- every one still running is cancelled and awaited, and then
    that failure is raised as itself.
    """
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
