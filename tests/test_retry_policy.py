"""Unit tests for the repo-owned LLM retry policy (providers/retry.py)."""

from __future__ import annotations

import asyncio

import pytest

import deep_research.providers.contracts as contracts_module
from deep_research.observability import RunTelemetryCollector, TokenUsage
from deep_research.providers.contracts import (
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from deep_research.providers.retry import with_retries
from deep_research.request_budget import (
    RequestAttemptLimitError,
    RequestBudget,
    RequestBudgetSnapshot,
)


def _recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_with_retries_succeeds_on_first_attempt(monkeypatch) -> None:
    slept = _recorded_sleeps(monkeypatch)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await with_retries(
        operation, retry_count=5, initial_delay=1.0, max_delay=16.0
    )
    assert result == "ok"
    assert calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_with_retries_backoff_is_exponential_and_capped(monkeypatch) -> None:
    slept = _recorded_sleeps(monkeypatch)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 5:
            raise ProviderTimeoutError("timeout")
        return "ok"

    result = await with_retries(
        operation, retry_count=5, initial_delay=1.0, max_delay=4.0
    )
    assert result == "ok"
    assert calls == 5
    assert slept == [1.0, 2.0, 4.0, 4.0]


@pytest.mark.asyncio
async def test_with_retries_raises_final_error_after_exhaustion(monkeypatch) -> None:
    slept = _recorded_sleeps(monkeypatch)

    async def operation() -> str:
        raise ProviderResponseError("boom", retryable=True, failure_origin="sdk")

    with pytest.raises(ProviderResponseError, match="boom"):
        await with_retries(operation, retry_count=3, initial_delay=1.0, max_delay=4.0)
    assert slept == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_with_retries_non_transient_errors_propagate_immediately(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await with_retries(operation, retry_count=5, initial_delay=1.0, max_delay=16.0)
    assert calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_with_retries_propagates_output_limit_without_retrying(
    monkeypatch,
) -> None:
    slept = _recorded_sleeps(monkeypatch)
    telemetry_type = getattr(contracts_module, "ProviderResponseTelemetry", None)
    error_type = getattr(contracts_module, "ProviderOutputLimitError", None)
    assert telemetry_type is not None
    assert error_type is not None
    telemetry = telemetry_type(
        finish_reason_category="length",
        configured_max_tokens=4096,
        usage=TokenUsage(input_tokens=8, output_tokens=4096),
        request_attempt=1,
    )
    error = error_type(telemetry)
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(ProviderResponseError):
        await with_retries(operation, retry_count=5, initial_delay=1.0, max_delay=16.0)

    assert calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_with_retries_never_retries_a_spent_request_budget(monkeypatch) -> None:
    """A refused attempt is a ceiling, not a transient failure.

    Today the refusal survives this loop only because ``_is_transient``
    recognises exactly three typed provider errors and nothing else. A future
    broadening of that predicate — the natural-looking way to "make retries
    more robust" — would silently retry a spent budget: every retry would ask
    the same budget for headroom it has already refused, so the declared
    ceiling would stop being a ceiling. This test freezes today's contract; it
    is not a licence to change ``retry.py``.
    """
    slept = _recorded_sleeps(monkeypatch)
    refusal = RequestAttemptLimitError(
        RequestBudgetSnapshot(
            provider="tavily",
            attempts=4,
            ceiling=4,
            effective_limit=4,
            input_tokens=0,
            output_tokens=0,
        )
    )
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise refusal

    with pytest.raises(RequestAttemptLimitError) as caught:
        await with_retries(
            operation, retry_count=5, initial_delay=1.0, max_delay=16.0
        )

    assert calls == 1
    assert caught.value is refusal
    assert slept == []


# ---------------------------------------------------------------------------
# §7.3 telemetry: the retry loop is where a 429 is counted, and where a
# reserved attempt that failed stops being a call in flight.
# ---------------------------------------------------------------------------


def _rate_limited_operation(recoveries: int):
    """An operation that answers 429 ``recoveries`` times, then succeeds."""
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= recoveries:
            raise ProviderRateLimitError("DeepSeek rate limit exceeded")
        return "ok"

    return operation


@pytest.mark.asyncio
async def test_with_retries_accounts_rate_limits_and_recoveries(monkeypatch) -> None:
    """Three transient rate-limit failures, two retried to success, one that
    exhausted the ladder: 3 errors, 2 recovered."""
    _recorded_sleeps(monkeypatch)
    collector = RunTelemetryCollector()

    for _ in range(2):
        await with_retries(
            _rate_limited_operation(1),
            retry_count=2,
            initial_delay=1.0,
            max_delay=4.0,
            telemetry=collector,
        )
    with pytest.raises(ProviderRateLimitError):
        await with_retries(
            _rate_limited_operation(1),
            retry_count=0,
            initial_delay=1.0,
            max_delay=4.0,
            telemetry=collector,
        )

    telemetry = collector.snapshot()
    assert telemetry.rate_limit_errors == 3
    assert telemetry.rate_limit_recovered == 2


@pytest.mark.asyncio
async def test_with_retries_releases_each_failed_attempt(monkeypatch) -> None:
    """Every attempt the loop gives up on releases its reservation.

    Half of this accounting lives in the retry loop: the budget's observer
    counts the reservation, and a failed attempt never reports tokens, so
    nothing else would ever release it. Three attempts that leaked would show
    up as three extra calls in flight the moment three more reserve.
    """
    _recorded_sleeps(monkeypatch)
    collector = RunTelemetryCollector()
    budget = RequestBudget()
    budget.set_observer(collector.observe_budget)

    async def operation() -> str:
        budget.reserve("deepseek")
        raise ProviderTimeoutError("DeepSeek request timed out")

    with pytest.raises(ProviderTimeoutError):
        await with_retries(
            operation,
            retry_count=2,
            initial_delay=1.0,
            max_delay=4.0,
            telemetry=collector,
        )

    for _ in range(3):
        budget.reserve("deepseek")

    assert collector.snapshot().peak_calls_in_flight == 3


@pytest.mark.asyncio
async def test_a_refused_attempt_is_not_reported_as_a_failed_attempt(
    monkeypatch,
) -> None:
    """The refusal holds no reservation to release, so it must not release one.

    A refused attempt never reached the wire, so it is the one failure with
    nothing to give back. Releasing for it anyway would take the gauge below
    the number of calls really in flight, and the peak would then read low by
    exactly that much.
    """
    _recorded_sleeps(monkeypatch)
    collector = RunTelemetryCollector()
    budget = RequestBudget()
    budget.set_observer(collector.observe_budget)
    budget.reserve("deepseek")  # one call already in flight elsewhere

    refusal = RequestAttemptLimitError(
        RequestBudgetSnapshot(
            provider="deepseek",
            attempts=1,
            ceiling=1,
            effective_limit=1,
            input_tokens=0,
            output_tokens=0,
        )
    )

    async def operation() -> str:
        raise refusal

    with pytest.raises(RequestAttemptLimitError):
        await with_retries(
            operation,
            retry_count=1,
            initial_delay=1.0,
            max_delay=4.0,
            telemetry=collector,
        )

    budget.reserve("deepseek")
    budget.reserve("deepseek")

    assert collector.snapshot().peak_calls_in_flight == 3


# ---------------------------------------------------------------------------
# Per-attempt records (stall-fix brief P1-B): ``with_retries`` is the one
# place that knows an attempt's number, timing and outcome, so it is where
# they are built rather than duplicated at every provider call site.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_retries_reports_a_timeout_then_an_ok_attempt(monkeypatch) -> None:
    """Two attempts recorded as timeout then ok, in order, with plausible timing."""
    _recorded_sleeps(monkeypatch)
    recorded: list[tuple[int, float, float, str]] = []
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderTimeoutError("DeepSeek request timed out")
        return "ok"

    result = await with_retries(
        operation,
        retry_count=2,
        initial_delay=1.0,
        max_delay=4.0,
        on_attempt=lambda *record: recorded.append(record),
    )

    assert result == "ok"
    assert [(number, outcome) for number, _, _, outcome in recorded] == [
        (1, "timeout"),
        (2, "ok"),
    ]
    for _, start_offset, duration, _ in recorded:
        assert start_offset >= 0.0
        assert duration >= 0.0
    # The second attempt starts no earlier than the first began.
    assert recorded[1][1] >= recorded[0][1]


@pytest.mark.asyncio
async def test_with_retries_reports_a_connection_error_outcome(monkeypatch) -> None:
    """An SDK connection failure is tagged ``connection error``, not its class name."""
    _recorded_sleeps(monkeypatch)
    recorded: list[tuple[int, float, float, str]] = []

    async def operation() -> str:
        raise ProviderResponseError(
            "DeepSeek connection failed",
            failure_origin="sdk",
            retryable=True,
            failure_category="transport",
        )

    with pytest.raises(ProviderResponseError):
        await with_retries(
            operation,
            retry_count=0,
            initial_delay=1.0,
            max_delay=4.0,
            on_attempt=lambda *record: recorded.append(record),
        )

    assert [outcome for _, _, _, outcome in recorded] == ["connection error"]


@pytest.mark.asyncio
async def test_with_retries_reports_another_error_class_name(monkeypatch) -> None:
    """A failure that is neither a timeout nor a connection error keeps its
    own exception class name, so the telemetry line can still name it."""
    _recorded_sleeps(monkeypatch)
    recorded: list[tuple[int, float, float, str]] = []

    async def operation() -> str:
        raise ProviderResponseError(
            "DeepSeek request failed with status 500",
            failure_origin="sdk",
            retryable=True,
            failure_category="http",
            http_status_code=500,
        )

    with pytest.raises(ProviderResponseError):
        await with_retries(
            operation,
            retry_count=0,
            initial_delay=1.0,
            max_delay=4.0,
            on_attempt=lambda *record: recorded.append(record),
        )

    assert [outcome for _, _, _, outcome in recorded] == ["ProviderResponseError"]


@pytest.mark.asyncio
async def test_with_retries_never_reports_an_attempt_for_a_refused_budget(
    monkeypatch,
) -> None:
    """A budget refusal never reached the wire, so it is not a transport attempt."""
    _recorded_sleeps(monkeypatch)
    recorded: list[tuple[int, float, float, str]] = []
    refusal = RequestAttemptLimitError(
        RequestBudgetSnapshot(
            provider="deepseek",
            attempts=1,
            ceiling=1,
            effective_limit=1,
            input_tokens=0,
            output_tokens=0,
        )
    )

    async def operation() -> str:
        raise refusal

    with pytest.raises(RequestAttemptLimitError):
        await with_retries(
            operation,
            retry_count=1,
            initial_delay=1.0,
            max_delay=4.0,
            on_attempt=lambda *record: recorded.append(record),
        )

    assert recorded == []
