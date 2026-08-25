"""Repo-owned transient-failure retry policy for provider calls.

The openai SDK pinned in this project only exposes ``max_retries`` with a
fixed internal backoff (0.5s base, 8s cap) and no way to configure either
value, so the retry policy is owned here: up to ``retry_count`` retries
with exponential backoff from ``retry_initial_delay`` seconds, capped at
``retry_max_delay`` seconds. Only genuinely transient provider failures are
retried — timeouts, rate limits, connection errors, and 408/409/429/5xx
statuses — while deterministic errors (other 4xx, malformed content,
configuration, validation) propagate immediately, unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from deep_research.providers.contracts import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)

T = TypeVar("T")


def _is_transient(error: BaseException) -> bool:
    """True only for failures the retry policy may retry."""
    if isinstance(error, (ProviderTimeoutError, ProviderRateLimitError)):
        return True
    if isinstance(error, ProviderResponseError):
        return error.retryable
    return False


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    retry_count: int,
    initial_delay: float,
    max_delay: float,
) -> T:
    """Run ``operation`` with exponential-backoff retries for transient errors.

    Total attempts are ``retry_count`` retries plus the initial attempt.
    The wait before retry ``attempt`` (0-based) is
    ``min(initial_delay * 2**attempt, max_delay)`` seconds. Non-transient
    errors propagate immediately; a transient error that persists past the
    last retry is re-raised as the final typed error.
    """
    for attempt in range(retry_count + 1):
        try:
            return await operation()
        except Exception as error:
            if not _is_transient(error):
                raise
            if attempt >= retry_count:
                raise
            delay = min(initial_delay * (2**attempt), max_delay)
            await asyncio.sleep(delay)
    raise AssertionError("retry loop did not return")
