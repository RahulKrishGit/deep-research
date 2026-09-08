"""Unit tests for the repo-owned LLM retry policy (providers/retry.py)."""

from __future__ import annotations

import asyncio

import pytest

import deep_research.providers.contracts as contracts_module
from deep_research.observability import TokenUsage
from deep_research.providers.contracts import (
    ProviderResponseError,
    ProviderTimeoutError,
)
from deep_research.providers.retry import with_retries


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
        raise ProviderResponseError("boom", retryable=True)

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
