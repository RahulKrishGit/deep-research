"""Tests for the request attempt budget.

``RequestBudget`` is what makes a declared spend ceiling real. A live canary
breached its declared Tavily ceiling by roughly eleven percent and nothing
stopped it, because the ceiling existed only as arithmetic in a plan
document: it was derived wrongly and no reader could see the spend while the
run was happening. These tests pin the two properties that close that gap —
the arithmetic that decides how many attempts the ceiling permits, and the
counting rules that keep a refused attempt from becoming one.

Every test here is offline. A reservation is counter arithmetic followed by a
comparison; no transport, provider, or network is involved.
"""

from __future__ import annotations

import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from deep_research.request_budget import (
    RequestAttemptLimitError,
    RequestBudget,
    RequestBudgetSnapshot,
    RequestBudgetUpdate,
)
from deep_research.utils.config import RequestBudgetConfig


def test_snapshots_are_frozen_and_carry_only_their_declared_fields() -> None:
    """A snapshot is publishable: immutable, and nothing but bounded values.

    The CLI streams these to stdout and ``ResearchOutcome`` keeps them, so a
    snapshot must be safe to hand out — no reference to the budget's mutable
    counters, and no field a caller could use to smuggle content through.
    """
    budget = RequestBudget(RequestBudgetConfig(tavily_attempt_ceiling=4))

    snapshot = budget.reserve("tavily")

    assert isinstance(snapshot, RequestBudgetSnapshot)
    assert [field.name for field in dataclasses.fields(snapshot)] == [
        "provider",
        "attempts",
        "ceiling",
        "effective_limit",
        "input_tokens",
        "output_tokens",
    ]
    assert snapshot.provider in ("deepseek", "openai", "tavily")
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.attempts = 99  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        budget.snapshots()[0].attempts = 99  # type: ignore[misc]
    assert isinstance(budget.snapshots(), tuple)


def test_a_published_snapshot_never_moves_with_the_budget() -> None:
    """Each read builds a fresh snapshot; no mutable state escapes.

    A counter object handed out twice would make the first reader's line
    rewrite itself as the run continued, which is exactly how a mid-run
    observation becomes untrustworthy.
    """
    budget = RequestBudget()

    first = budget.reserve("tavily")
    second = budget.reserve("tavily")

    assert first is not second
    assert (first.attempts, second.attempts) == (1, 2)
    assert budget.snapshot("tavily").attempts == 2


@pytest.mark.parametrize(
    ("ceiling", "stop_fraction", "expected_limit"),
    [
        (10, 0.5, 5),
        (7, 0.5, 3),
        (3, 0.9, 2),
        (5, 1.0, 5),
        (2, 0.5, 1),
        (100, 0.9, 90),
    ],
)
def test_floor_arithmetic_permits_the_floor_and_refuses_the_next_attempt(
    ceiling: int,
    stop_fraction: float,
    expected_limit: int,
) -> None:
    """``floor(ceiling * stop_fraction)`` attempts are permitted, and no more."""
    budget = RequestBudget(
        RequestBudgetConfig(tavily_attempt_ceiling=ceiling, stop_fraction=stop_fraction)
    )

    for attempt in range(1, expected_limit + 1):
        assert budget.reserve("tavily").attempts == attempt

    with pytest.raises(RequestAttemptLimitError):
        budget.reserve("tavily")

    spent = budget.snapshot("tavily")
    assert spent.attempts == expected_limit
    assert spent.ceiling == ceiling
    assert spent.effective_limit == expected_limit


def test_a_zero_effective_limit_refuses_the_very_first_attempt() -> None:
    """``floor(1 * 0.5) == 0`` is a valid declaration: no attempt may go out.

    A stop fraction below ``1 / ceiling`` is not a rounding artefact to
    tolerate. Permitting the first attempt because a zero limit "looks
    unset" is the same approximate enforcement this primitive replaces.
    """
    budget = RequestBudget(
        RequestBudgetConfig(tavily_attempt_ceiling=1, stop_fraction=0.5)
    )

    with pytest.raises(RequestAttemptLimitError) as refused:
        budget.reserve("tavily")

    assert refused.value.snapshot.effective_limit == 0
    assert refused.value.snapshot.attempts == 0
    assert budget.snapshot("tavily").attempts == 0


def test_a_refused_attempt_does_not_increment_the_counter() -> None:
    """Refusals are not attempts: a retry loop cannot manufacture headroom."""
    budget = RequestBudget(RequestBudgetConfig(deepseek_attempt_ceiling=2))

    budget.reserve("deepseek")
    budget.reserve("deepseek")

    for _ in range(5):
        with pytest.raises(RequestAttemptLimitError):
            budget.reserve("deepseek")

    assert budget.snapshot("deepseek").attempts == 2


def test_an_absent_ceiling_counts_attempts_and_never_refuses() -> None:
    """No declared ceiling is a counted, unbounded budget — not a silent zero."""
    budget = RequestBudget(RequestBudgetConfig(deepseek_attempt_ceiling=3))

    for _ in range(50):
        budget.reserve("tavily")

    unbounded = budget.snapshot("tavily")
    assert unbounded.attempts == 50
    assert unbounded.ceiling is None
    assert unbounded.effective_limit is None

    # The declared ceiling binds only its own category.
    for _ in range(3):
        budget.reserve("deepseek")
    with pytest.raises(RequestAttemptLimitError):
        budget.reserve("deepseek")


def test_token_totals_accumulate_per_provider() -> None:
    """Reported usage accumulates per category, input and output apart."""
    budget = RequestBudget()

    first = budget.record_tokens("deepseek", input_tokens=120, output_tokens=30)
    second = budget.record_tokens("deepseek", input_tokens=5, output_tokens=7)

    assert (first.input_tokens, first.output_tokens) == (120, 30)
    assert (second.input_tokens, second.output_tokens) == (125, 37)
    untouched = budget.snapshot("openai")
    assert (untouched.input_tokens, untouched.output_tokens) == (0, 0)


def test_snapshots_are_ordered_deepseek_openai_tavily() -> None:
    """A whole-budget read is deterministic, and each entry is its own provider."""
    budget = RequestBudget()

    for _ in range(1):
        budget.reserve("deepseek")
    for _ in range(2):
        budget.reserve("openai")
    for _ in range(3):
        budget.reserve("tavily")

    ordered = budget.snapshots()

    assert [snapshot.provider for snapshot in ordered] == [
        "deepseek",
        "openai",
        "tavily",
    ]
    assert [snapshot.attempts for snapshot in ordered] == [1, 2, 3]
    assert budget.snapshots() == ordered


def test_observers_receive_reserved_token_and_blocked_updates() -> None:
    """Every state change publishes exactly one update of the right kind."""
    budget = RequestBudget(RequestBudgetConfig(tavily_attempt_ceiling=1))
    updates: list[RequestBudgetUpdate] = []
    budget.set_observer(updates.append)

    budget.reserve("tavily")
    budget.record_tokens("tavily", input_tokens=11, output_tokens=2)
    with pytest.raises(RequestAttemptLimitError):
        budget.reserve("tavily")

    assert [update.kind for update in updates] == [
        "attempt_reserved",
        "tokens_reported",
        "attempt_blocked",
    ]
    assert isinstance(updates[0], RequestBudgetUpdate)
    assert [update.snapshot.attempts for update in updates] == [1, 1, 1]
    assert updates[1].snapshot.input_tokens == 11
    assert updates[2].snapshot.effective_limit == 1


def test_clearing_the_observer_stops_the_updates() -> None:
    budget = RequestBudget()
    updates: list[RequestBudgetUpdate] = []
    budget.set_observer(updates.append)

    budget.reserve("openai")
    budget.set_observer(None)
    budget.reserve("openai")

    assert len(updates) == 1


def test_a_failing_observer_neither_refuses_a_permitted_attempt_nor_masks_the_limit(
) -> None:
    """Reporting is best effort; enforcement is the contract.

    A broken CLI stream handler must not be able to do either of the two
    things that would matter: refuse an attempt the budget already permitted,
    or replace ``RequestAttemptLimitError`` with its own exception.
    """
    budget = RequestBudget(RequestBudgetConfig(deepseek_attempt_ceiling=1))

    def exploding_observer(update: RequestBudgetUpdate) -> None:
        raise RuntimeError("observer is broken")

    budget.set_observer(exploding_observer)

    permitted = budget.reserve("deepseek")
    recorded = budget.record_tokens("deepseek", input_tokens=1, output_tokens=1)

    assert permitted.attempts == 1
    assert recorded.attempts == 1
    with pytest.raises(RequestAttemptLimitError) as refused:
        budget.reserve("deepseek")

    assert refused.value.snapshot.attempts == 1


def test_the_limit_error_carries_its_snapshot_and_only_static_text() -> None:
    """The error reaches operators, so its message is project text, not inputs."""
    spent = RequestAttemptLimitError(
        RequestBudgetSnapshot(
            provider="deepseek",
            attempts=7,
            ceiling=7,
            effective_limit=7,
            input_tokens=1234,
            output_tokens=5678,
        )
    )
    blocked = RequestAttemptLimitError(
        RequestBudgetSnapshot(
            provider="tavily",
            attempts=0,
            ceiling=1,
            effective_limit=0,
            input_tokens=0,
            output_tokens=0,
        )
    )

    assert spent.snapshot.provider == "deepseek"
    assert spent.snapshot.ceiling == 7
    assert str(spent) == str(blocked)
    assert str(spent).strip() != ""


def test_observers_are_called_only_after_the_state_lock_is_released() -> None:
    """An observer may read the budget back without deadlocking.

    The state lock covers the counter arithmetic and nothing else. If it were
    still held across the callback, a handler that reads the budget — which is
    what the CLI's stream does — would deadlock the run.
    """
    budget = RequestBudget(RequestBudgetConfig(tavily_attempt_ceiling=3))
    observed: list[tuple[RequestBudgetSnapshot, ...]] = []
    budget.set_observer(lambda update: observed.append(budget.snapshots()))

    worker = threading.Thread(target=lambda: budget.reserve("tavily"), daemon=True)
    worker.start()
    worker.join(timeout=5.0)

    assert not worker.is_alive(), "the observer ran while the state lock was held"
    assert observed
    assert [snapshot.attempts for snapshot in observed[0]] == [0, 0, 1]


def test_parallel_reservations_never_exceed_the_effective_limit() -> None:
    """Concurrent callers share one counter and the ceiling still holds.

    Provider calls really are concurrent: ``WebSearchTool`` runs its client
    call in a worker thread, and several agents can be mid-request at once.
    A read-then-increment that is not atomic would grant more attempts than
    the ceiling allows and make the declared ceiling approximately enforced
    again.
    """
    budget = RequestBudget(RequestBudgetConfig(tavily_attempt_ceiling=8))
    workers = 24
    start = threading.Barrier(workers)
    results_lock = threading.Lock()
    granted = 0
    refused = 0

    def attempt() -> None:
        nonlocal granted, refused
        start.wait(timeout=10.0)
        try:
            budget.reserve("tavily")
        except RequestAttemptLimitError:
            with results_lock:
                refused += 1
        else:
            with results_lock:
                granted += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda index: attempt(), range(workers)))

    assert granted == 8
    assert refused == workers - 8
    assert budget.snapshot("tavily").attempts == 8
