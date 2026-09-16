"""Run-scoped bounds on transport attempts per provider category.

A declared spend ceiling is worthless while it exists only as arithmetic in a
plan document: a live canary breached its declared Tavily ceiling by roughly
eleven percent and nothing in the running system noticed. ``RequestBudget`` is
what makes a ceiling real. Every transport attempt reserves one unit *before*
the call goes out, and the attempt past the effective limit is refused before
any network I/O happens.

Three properties carry that weight:

* the limit is ``floor(ceiling * stop_fraction)``, computed the same way for
  every reader, and a zero limit is a real declaration that refuses the very
  first attempt;
* a refused attempt never increments the counter, so a retry loop cannot
  manufacture headroom;
* reservations are atomic, because provider calls really are concurrent.

The budget holds bounded integers only. It is not a report, an audit log, or a
place to put provider payloads.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers only
    from deep_research.utils.config import RequestBudgetConfig

__all__ = [
    "ProviderCategory",
    "RequestAttemptLimitError",
    "RequestBudget",
    "RequestBudgetEventKind",
    "RequestBudgetObserver",
    "RequestBudgetSnapshot",
    "RequestBudgetUpdate",
]

ProviderCategory = Literal["deepseek", "openai", "tavily"]
RequestBudgetEventKind = Literal[
    "attempt_reserved",
    "tokens_reported",
    "attempt_blocked",
]

#: Publication order for :meth:`RequestBudget.snapshots`. Deterministic so that
#: two reads of the same budget always render in the same order.
_PROVIDER_ORDER: tuple[ProviderCategory, ...] = ("deepseek", "openai", "tavily")

_LIMIT_MESSAGE = (
    "Request attempt limit reached: this provider's declared attempt ceiling "
    "is spent, so no further transport attempt was made."
)


@dataclass(frozen=True, slots=True)
class RequestBudgetSnapshot:
    """Immutable view of one provider category's budget state."""

    provider: ProviderCategory
    attempts: int
    ceiling: int | None
    effective_limit: int | None
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class RequestBudgetUpdate:
    """One published state change."""

    kind: RequestBudgetEventKind
    snapshot: RequestBudgetSnapshot


RequestBudgetObserver = Callable[[RequestBudgetUpdate], None]


class RequestAttemptLimitError(RuntimeError):
    """Raised instead of making the transport attempt past the limit.

    The message is static project text: it never interpolates provider names,
    ceilings, or counts. The machine-readable reason travels in
    :attr:`snapshot` instead.
    """

    snapshot: RequestBudgetSnapshot

    def __init__(self, snapshot: RequestBudgetSnapshot) -> None:
        super().__init__(_LIMIT_MESSAGE)
        self.snapshot = snapshot

    def __str__(self) -> str:
        return _LIMIT_MESSAGE


@dataclass(slots=True)
class _ProviderState:
    """Mutable counters for a single provider category."""

    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class RequestBudget:
    """Counts reserved attempts and reported tokens for one run.

    One instance is shared by every provider of a run, so the ceilings in
    ``config`` apply to the run as a whole rather than to a single agent.
    """

    def __init__(self, config: "RequestBudgetConfig | None" = None) -> None:
        self._ceilings: dict[ProviderCategory, int | None] = {
            "deepseek": None if config is None else config.deepseek_attempt_ceiling,
            "openai": None if config is None else config.openai_attempt_ceiling,
            "tavily": None if config is None else config.tavily_attempt_ceiling,
        }
        self._stop_fraction = 1.0 if config is None else config.stop_fraction
        self._states: dict[ProviderCategory, _ProviderState] = {
            provider: _ProviderState() for provider in _PROVIDER_ORDER
        }
        self._lock = threading.Lock()
        self._observer: RequestBudgetObserver | None = None

    def reserve(self, provider: ProviderCategory) -> RequestBudgetSnapshot:
        """Reserve one attempt for ``provider``.

        Returns the post-reservation snapshot, or raises
        :class:`RequestAttemptLimitError` — before any transport attempt — when
        the effective limit is already spent. A refused attempt does not change
        any counter.
        """
        refusal: RequestAttemptLimitError | None = None
        update: RequestBudgetUpdate
        with self._lock:
            state = self._states[provider]
            limit = self._effective_limit(provider)
            if limit is not None and state.attempts >= limit:
                snapshot = self._snapshot_locked(provider)
                refusal = RequestAttemptLimitError(snapshot)
                update = RequestBudgetUpdate(kind="attempt_blocked", snapshot=snapshot)
            else:
                state.attempts += 1
                snapshot = self._snapshot_locked(provider)
                update = RequestBudgetUpdate(kind="attempt_reserved", snapshot=snapshot)
        # Notification happens outside the lock so that an observer may read
        # the budget back, and never masks the refusal above.
        self._notify(update)
        if refusal is not None:
            raise refusal
        return snapshot

    def record_tokens(
        self,
        provider: ProviderCategory,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> RequestBudgetSnapshot:
        """Add reported token usage for ``provider`` to the run totals."""
        with self._lock:
            state = self._states[provider]
            state.input_tokens += input_tokens
            state.output_tokens += output_tokens
            snapshot = self._snapshot_locked(provider)
            update = RequestBudgetUpdate(kind="tokens_reported", snapshot=snapshot)
        self._notify(update)
        return snapshot

    def snapshot(self, provider: ProviderCategory) -> RequestBudgetSnapshot:
        """Read one provider category's current state."""
        with self._lock:
            return self._snapshot_locked(provider)

    def snapshots(self) -> tuple[RequestBudgetSnapshot, ...]:
        """Read the whole budget in DeepSeek, OpenAI, Tavily order."""
        with self._lock:
            return tuple(
                self._snapshot_locked(provider) for provider in _PROVIDER_ORDER
            )

    def set_observer(self, observer: RequestBudgetObserver | None) -> None:
        """Install or clear the update observer. ``None`` stops notifications."""
        with self._lock:
            self._observer = observer

    def _effective_limit(self, provider: ProviderCategory) -> int | None:
        """``floor(ceiling * stop_fraction)``, or ``None`` when uncapped."""
        ceiling = self._ceilings[provider]
        if ceiling is None:
            return None
        return math.floor(ceiling * self._stop_fraction)

    def _snapshot_locked(self, provider: ProviderCategory) -> RequestBudgetSnapshot:
        """Build a fresh snapshot. Caller must hold the state lock."""
        state = self._states[provider]
        ceiling = self._ceilings[provider]
        return RequestBudgetSnapshot(
            provider=provider,
            attempts=state.attempts,
            ceiling=ceiling,
            effective_limit=self._effective_limit(provider),
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
        )

    def _notify(self, update: RequestBudgetUpdate) -> None:
        """Publish ``update``, best effort, with the state lock released.

        A broken observer must not be able to refuse an attempt the budget
        already permitted, nor replace the limit error with its own exception.
        """
        with self._lock:
            observer = self._observer
        if observer is None:
            return
        try:
            observer(update)
        except Exception:  # noqa: BLE001 - observers are best effort by contract
            return
