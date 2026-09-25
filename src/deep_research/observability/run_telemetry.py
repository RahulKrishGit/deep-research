"""Run-scoped concurrency and budget telemetry (spec §7.3, decisions D8/D9).

One run's four measurements — rate-limit errors and how many a retry recovered,
the peak number of provider calls in flight, each stage's calls and seconds,
and each operation's output tokens against the cap that bounds it — are
collected here from seams that already exist:

* the retry loop (:mod:`deep_research.providers.retry`) sees every transient
  failure, so it is where a 429 is counted and where a call's later success
  marks its 429s recovered;
* the providers' token-reporting call sites record one call each: the agent,
  the wall seconds, the reported output tokens, the configured cap and whether
  the reply hit it;
* the run's :class:`~deep_research.request_budget.RequestBudget` publishes its
  reservations and token reports to this object as its observer, which is what
  makes the peak a fact about the run rather than about one adapter.

Nothing here changes a cap or a concurrency limit. §7.3 asks for telemetry and
advice for the operator; §12 forbids automatic adjustment, and a measurement
that fed back into the run would make every figure it reports suspect.

One lock guards every counter, and no lock is ever held across an ``await``:
sub-topic research and verification batches really do run concurrently (Task
4.13), so these counters are shared mutable state.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from deep_research.observability.metrics import LLMOperation
from deep_research.utils.types import (
    OperationTelemetry,
    RunTelemetry,
    StageTelemetry,
)

if TYPE_CHECKING:  # pragma: no cover - import cost only, never a cycle
    from deep_research.request_budget import RequestBudgetUpdate

__all__ = [
    "RunTelemetryCollector",
    "cap_key_for",
    "render_telemetry_advice",
    "render_telemetry_line",
]

#: The stage name for a provider call that carried no agent name. Production
#: calls always carry one; evaluation and ad-hoc harnesses need not.
UNATTRIBUTED_AGENT = "unattributed"

#: The concurrency knob each agent's calls are bounded by (§7.3). The three are
#: the caps a run can be fanning out under; anything else at the peak falls
#: back to the researcher's, the cap that bounds the widest fan-out.
_CONCURRENCY_KNOBS = {
    "evidence_verifier": "agents.verifier_concurrency",
    "researcher": "agents.sub_topic_concurrency",
    "source_evaluator": "agents.source_scoring_concurrency",
}
_FALLBACK_CONCURRENCY_KNOB = "agents.sub_topic_concurrency"

#: The config key that bounds an operation's output tokens, in the plan's order:
#: every ReAct decision shares one cap, the planner's plan requests and the
#: Report Reviewer's reviews have their own, and everything else is bounded by
#: the global ``llm.max_tokens``.
_REACT_DECISION_CAP_KEY = "agents.react_decision_max_tokens"
_AGENT_CAP_KEYS = {
    "planner": "agents.planner_final_max_tokens",
    "report_reviewer": "agents.report_review_max_tokens",
}
_DEFAULT_CAP_KEY = "llm.max_tokens"

#: The provider categories whose calls report tokens, and so have both halves
#: of a call in flight: a reservation and a completion. A Tavily search
#: reserves against the same run budget but never reports tokens, so counting
#: it would hold the gauge one call high per search.
_MODEL_PROVIDER_CATEGORIES = frozenset({"deepseek", "openai"})

#: A call this close to its cap is what the advice line is for (§7.3).
_NEAR_CAP_PERCENT = 90


def cap_key_for(agent: str | None, operation: LLMOperation) -> str:
    """The config key that bounds one provider call's output tokens."""
    if operation == "react_tool_turn":
        return _REACT_DECISION_CAP_KEY
    if agent in _AGENT_CAP_KEYS:
        return _AGENT_CAP_KEYS[agent]
    return _DEFAULT_CAP_KEY


@dataclass(slots=True)
class _OperationAccumulator:
    """Output-token usage for one agent operation, as it accumulates."""

    configured_cap: int
    max_output_tokens: int = 0
    truncations: int = 0


@dataclass(slots=True)
class _StageAccumulator:
    """One agent's calls, as they accumulate."""

    calls: int = 0
    seconds: float = 0.0
    slowest_seconds: float = 0.0
    operations: dict[str, _OperationAccumulator] = field(default_factory=dict)


class RunTelemetryCollector:
    """Collects one run's §7.3 telemetry from the provider seams.

    Every method is safe to call from concurrent tasks. A provider built
    without a collector gets a private instance of this class, so an
    un-instrumented harness records into an object nobody reads and behaves
    exactly as it did before.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls_in_flight = 0
        self._peak_calls_in_flight = 0
        self._peak_agent: str | None = None
        self._starting_agent: str | None = None
        self._rate_limit_errors = 0
        self._rate_limit_recovered = 0
        self._stages: dict[str, _StageAccumulator] = {}

    def note_call_starting(self, agent: str | None) -> None:
        """Name the agent whose call is about to reserve a transport attempt.

        A :class:`~deep_research.request_budget.RequestBudgetUpdate` carries no
        agent, so the reservation ``observe_budget`` receives is attributed to
        the caller announced here. Two agents' calls can interleave between the
        announcement and the reservation it belongs to; the peak then names the
        other agent, which is one call of attribution drift on a figure whose
        only use is choosing which cap to look at.
        """
        with self._lock:
            self._starting_agent = agent

    def observe_budget(self, update: RequestBudgetUpdate) -> None:
        """Follow the run's attempt stream, counting model calls in flight.

        A reservation is one call in flight, and a token report is the
        completion of one — a report exists exactly when a call returned and
        spent something. A refused attempt reserved nothing (the budget refuses
        it before any I/O), so it changes nothing here. A search reservation is
        not counted: it has no token report that could ever release it.
        """
        if update.snapshot.provider not in _MODEL_PROVIDER_CATEGORIES:
            return
        if update.kind == "attempt_reserved":
            with self._lock:
                self._calls_in_flight += 1
                if self._calls_in_flight > self._peak_calls_in_flight:
                    self._peak_calls_in_flight = self._calls_in_flight
                    self._peak_agent = self._starting_agent
            return
        if update.kind == "tokens_reported":
            self.note_attempt_finished()

    def note_attempt_finished(self) -> None:
        """Release one reserved attempt that is over.

        The transport failures release here through the retry loop, which sees
        every attempt that failed; the completion half arrives as the budget's
        ``tokens_reported`` update. An attempt that is released by neither --
        a response that arrived but whose usage could not be read -- is
        released by the provider that rejected it. Without this, the gauge
        would stay a call high for every failure and a later peak would read
        high by that much.
        """
        with self._lock:
            if self._calls_in_flight > 0:
                self._calls_in_flight -= 1

    def note_rate_limit(self) -> None:
        """Count one provider rate-limit (429) error."""
        with self._lock:
            self._rate_limit_errors += 1

    def note_rate_limit_recovered(self, count: int = 1) -> None:
        """Count 429s of one call that a later attempt of that call recovered."""
        with self._lock:
            self._rate_limit_recovered += count

    def record_call(
        self,
        *,
        agent: str | None,
        operation: LLMOperation,
        seconds: float,
        output_tokens: int,
        configured_cap: int,
        truncated: bool,
    ) -> None:
        """Record one provider call that returned and reported its usage.

        ``seconds`` is the call's own wall time, retries and their backoff
        included: it is what the stage's runtime is made of. ``operation``
        decides which config key bounds the call, so the record says which knob
        an operator would raise.
        """
        stage_name = agent or UNATTRIBUTED_AGENT
        cap_key = cap_key_for(agent, operation)
        with self._lock:
            stage = self._stages.get(stage_name)
            if stage is None:
                stage = _StageAccumulator()
                self._stages[stage_name] = stage
            stage.calls += 1
            stage.seconds += seconds
            if seconds > stage.slowest_seconds:
                stage.slowest_seconds = seconds
            usage = stage.operations.get(cap_key)
            if usage is None:
                usage = _OperationAccumulator(configured_cap=configured_cap)
                stage.operations[cap_key] = usage
            if output_tokens > usage.max_output_tokens:
                usage.max_output_tokens = output_tokens
                # The cap that bounded the largest reply: the caller's own
                # configured value, which is the one an operator would raise.
                usage.configured_cap = configured_cap
            if truncated:
                usage.truncations += 1

    def snapshot(self) -> RunTelemetry:
        """Read the telemetry so far. Stages and operations are ordered."""
        with self._lock:
            stages = tuple(
                StageTelemetry(
                    agent=agent,
                    calls=stage.calls,
                    seconds=round(stage.seconds, 1),
                    slowest_seconds=round(stage.slowest_seconds, 1),
                    operations=tuple(
                        OperationTelemetry(
                            agent=agent,
                            max_output_tokens=usage.max_output_tokens,
                            configured_cap=usage.configured_cap,
                            truncations=usage.truncations,
                            cap_key=cap_key,
                        )
                        for cap_key, usage in sorted(stage.operations.items())
                    ),
                )
                for agent, stage in sorted(self._stages.items())
            )
            return RunTelemetry(
                rate_limit_errors=self._rate_limit_errors,
                rate_limit_recovered=self._rate_limit_recovered,
                peak_calls_in_flight=self._peak_calls_in_flight,
                peak_agent=self._peak_agent,
                stages=stages,
            )


def _operations(telemetry: RunTelemetry) -> tuple[OperationTelemetry, ...]:
    return tuple(
        operation for stage in telemetry.stages for operation in stage.operations
    )


def _slowest_stage(telemetry: RunTelemetry) -> StageTelemetry | None:
    return max(telemetry.stages, key=lambda stage: stage.slowest_seconds, default=None)


def _fullest_operation(telemetry: RunTelemetry) -> OperationTelemetry | None:
    """The operation that came closest to its cap, ties to the larger reply."""
    return max(
        _operations(telemetry),
        key=lambda operation: (
            _cap_share(operation),
            operation.max_output_tokens,
            operation.truncations,
        ),
        default=None,
    )


def _cap_share(operation: OperationTelemetry) -> int:
    """The percentage of its cap a reply used, truncated so it never reads high."""
    return operation.max_output_tokens * 100 // operation.configured_cap


def _truncations(telemetry: RunTelemetry) -> int:
    return sum(operation.truncations for operation in _operations(telemetry))


def render_telemetry_line(telemetry: RunTelemetry) -> str:
    """Render the run's §7.3 figures as the one CLI summary line.

    The line carries the four groups of §7.3 in a fixed order, so the same run
    always renders it the same way: the peak and the agent that set it, the
    rate limits and how many came back, the slowest call, the operation
    closest to its cap, and the truncation count.
    """
    peak = f"peak {telemetry.peak_calls_in_flight} provider calls in flight"
    if telemetry.peak_agent is not None:
        peak = f"{peak} ({telemetry.peak_agent})"
    parts = [
        peak,
        f"{telemetry.rate_limit_errors} rate limits "
        f"({telemetry.rate_limit_recovered} recovered)",
    ]
    slowest = _slowest_stage(telemetry)
    if slowest is not None:
        parts.append(f"slowest call {slowest.agent} {slowest.slowest_seconds:.1f} s")
    fullest = _fullest_operation(telemetry)
    if fullest is not None:
        parts.append(
            f"{fullest.agent} output {fullest.max_output_tokens:,} of "
            f"{fullest.configured_cap:,} tokens ({_cap_share(fullest)}% of its cap)"
        )
    parts.append(f"{_truncations(telemetry)} truncated")
    return f"Telemetry: {'; '.join(parts)}"


def render_telemetry_advice(telemetry: RunTelemetry) -> tuple[str, ...]:
    """The §7.3 advice this telemetry triggers, in a fixed order.

    Advice only: the run never acts on it, and nothing is auto-tuned (§12).
    A run with no rate limits and no operation near or over its cap produces
    no lines at all.
    """
    advice: list[str] = []
    if telemetry.rate_limit_errors > 0:
        knob = _CONCURRENCY_KNOBS.get(
            telemetry.peak_agent or "", _FALLBACK_CONCURRENCY_KNOB
        )
        advice.append(
            f"rate limits hit {telemetry.rate_limit_errors} times; "
            f"consider lowering {knob}"
        )
    fullest = _fullest_operation(telemetry)
    if fullest is not None and (
        _cap_share(fullest) >= _NEAR_CAP_PERCENT or fullest.truncations > 0
    ):
        advice.append(
            f"output within {_cap_share(fullest)}% of the {fullest.agent} cap; "
            "consider raising it"
        )
    return tuple(advice)
