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
from time import perf_counter
from typing import TYPE_CHECKING, TypeAlias, TypeVar

from deep_research.providers.contracts import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from deep_research.request_budget import RequestAttemptLimitError

if TYPE_CHECKING:  # pragma: no cover - import cost only, never a cycle
    from deep_research.observability.run_telemetry import RunTelemetryCollector

T = TypeVar("T")

#: One transport attempt's number (1-based), start offset and duration in
#: seconds relative to the call's own start, and outcome (``on_attempt`` is
#: called with exactly these four positional values, in this order).
AttemptObserver: TypeAlias = Callable[[int, float, float, str], None]


def _attempt_outcome(error: BaseException) -> str:
    """One safe outcome tag for a failed transport attempt.

    ``"timeout"`` and ``"connection error"`` name the two failure shapes that
    matter when diagnosing a stalled call;
    anything else keeps its own exception class name, which stays safe here
    because every error this loop sees is already a project-owned typed
    failure whose message carries no provider content.
    """
    if isinstance(error, ProviderTimeoutError):
        return "timeout"
    if isinstance(error, ProviderResponseError) and error.failure_category == "transport":
        return "connection error"
    return type(error).__name__


def _is_transient(error: BaseException) -> bool:
    """True only for failures the retry policy may retry."""
    if isinstance(error, (ProviderTimeoutError, ProviderRateLimitError)):
        return True
    if isinstance(error, ProviderResponseError):
        return error.retryable
    return False


def _drop_chained_provider_state(error: BaseException) -> None:
    """Detach the SDK exception this typed error was chained to.

    The SDK's own exceptions retain ``request`` (the prompt and the tool
    definitions) and ``body`` (the decoded provider response). A typed error
    raised inside an ``except`` block picks up that SDK exception through both
    ``__cause__`` and ``__context__`` — and ``__context__`` is re-populated by
    the interpreter at raise time, so ``from None`` does not remove it. The
    only reliable point to break the link is a raise that happens outside any
    handler, which is where this runs.

    This detaches the *chain*, and it is no longer the only measure in place.
    The provider translators return their typed error instead of raising it, so
    their frames never land on the new error's traceback still holding the SDK
    exception as a parameter local; and the interpreter's implicit
    ``del error`` clears the handler's own local as that handler exits. What is
    left for this function is the ``__cause__``/``__context__`` link.
    """
    error.__cause__ = None
    error.__context__ = None
    error.__suppress_context__ = True


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    retry_count: int,
    initial_delay: float,
    max_delay: float,
    telemetry: RunTelemetryCollector | None = None,
    on_attempt: AttemptObserver | None = None,
) -> T:
    """Run ``operation`` with exponential-backoff retries for transient errors.

    Total attempts are ``retry_count`` retries plus the initial attempt.
    The wait before retry ``attempt`` (0-based) is
    ``min(initial_delay * 2**attempt, max_delay)`` seconds. Non-transient
    errors propagate immediately; a transient error that persists past the
    last retry is re-raised as the final typed error, with no SDK exception
    chained behind it.

    ``telemetry`` receives this loop's two contributions to the run's telemetry. Each
    :class:`~deep_research.providers.contracts.ProviderRateLimitError` is one
    429, and a call whose later attempt succeeds has each of its 429s marked
    recovered — recovery is a property of the call, not of an attempt, because
    the caller is what got its answer. Every failed attempt also releases the
    reservation it made: nothing else can, since a failed attempt never reports
    tokens. A refused attempt is the exception — the budget refused it before
    any I/O, so it holds nothing to release. ``None`` counts nothing, which is
    what a caller without telemetry gets.

    ``on_attempt`` receives this loop's per-attempt contribution to the call
    telemetry: every transport attempt's
    number, its start offset and duration relative to this call, and its
    outcome (``"ok"`` or :func:`_attempt_outcome`). This is the one place that
    already knows all four, so callers never time attempts themselves. A
    refused attempt never reached the wire and is not reported here, for the
    same reason it releases no reservation above.
    """
    failure: BaseException | None = None
    rate_limits = 0
    call_started_at = perf_counter()
    for attempt in range(retry_count + 1):
        attempt_started_at = perf_counter()
        try:
            result = await operation()
        except Exception as error:
            if isinstance(error, RequestAttemptLimitError):
                failure = error
                break
            if on_attempt is not None:
                on_attempt(
                    attempt + 1,
                    attempt_started_at - call_started_at,
                    perf_counter() - attempt_started_at,
                    _attempt_outcome(error),
                )
            if isinstance(error, ProviderRateLimitError):
                rate_limits += 1
                if telemetry is not None:
                    telemetry.note_rate_limit()
            if telemetry is not None:
                telemetry.note_attempt_finished()
            if not _is_transient(error) or attempt >= retry_count:
                failure = error
                break
            delay = min(initial_delay * (2**attempt), max_delay)
            await asyncio.sleep(delay)
        else:
            if on_attempt is not None:
                on_attempt(
                    attempt + 1,
                    attempt_started_at - call_started_at,
                    perf_counter() - attempt_started_at,
                    "ok",
                )
            if telemetry is not None and rate_limits:
                telemetry.note_rate_limit_recovered(rate_limits)
            return result
    if failure is None:
        raise AssertionError("retry loop did not return")
    # Outside the handler on purpose: see ``_drop_chained_provider_state``.
    _drop_chained_provider_state(failure)
    raise failure
