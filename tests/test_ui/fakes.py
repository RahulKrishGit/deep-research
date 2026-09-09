"""Offline synchronous runner fakes for local UI controller tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from threading import Event
from typing import Any

from deep_research.observability import TokenUsage
from deep_research.runtime.outcome import ResearchOutcome, ToolCallSummary
from deep_research.utils.types import (
    Claim,
    Finding,
    ResearchError,
    ResearchEvent,
    ResearchState,
    ScoredSource,
)


def make_outcome(
    *,
    session_id: str = "a" * 32,
    question: str = "Question",
    status: str = "completed",
    iteration: int = 1,
    max_iterations: int = 3,
    report_path: str | None = None,
    trace_url: str | None = None,
    report: str | None = "# Research report",
    errors: Sequence[ResearchError] = (),
    events: Sequence[ResearchEvent] = (),
    raw_findings: Sequence[Finding] = (),
    evaluated_sources: Sequence[ScoredSource] = (),
    verified_claims: Sequence[Claim] = (),
    token_usage: TokenUsage | None = None,
    tool_calls: Sequence[ToolCallSummary] = (),
) -> ResearchOutcome:
    """Build a real outcome without providers, graph execution, or I/O."""
    state = ResearchState(
        session_id=session_id,
        original_question=question,
        iteration=iteration,
        max_iterations=max_iterations,
        report=report,
        errors=list(errors),
        events=list(events),
        raw_findings=list(raw_findings),
        evaluated_sources=list(evaluated_sources),
        verified_claims=list(verified_claims),
    )
    return ResearchOutcome(
        session_id=session_id,
        question=question,
        status=status,
        state=state,
        trace_url=trace_url,
        report_path=report_path,
        token_usage=token_usage or TokenUsage(input_tokens=0, output_tokens=0),
        tool_calls=tuple(tool_calls),
    )


class GatedSyncRunner:
    """Publish events, signal start, then wait for explicit release."""

    def __init__(
        self,
        *,
        events: Sequence[ResearchEvent] = (),
        outcome_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self.events = list(events)
        self.outcome_kwargs = dict(outcome_kwargs or {})
        self.started = Event()
        self.release = Event()
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        question: str,
        session_id: str,
        event_handler: Callable[[ResearchEvent], None] | None = None,
        **kwargs: Any,
    ) -> ResearchOutcome:
        self.calls.append(
            {
                "question": question,
                "session_id": session_id,
                "event_handler": event_handler,
                **kwargs,
            }
        )
        if event_handler is not None:
            for event in self.events:
                event_handler(event)
        self.started.set()
        self.release.wait()
        return make_outcome(
            question=question,
            session_id=session_id,
            events=self.events,
            **self.outcome_kwargs,
        )


class FailingSyncRunner:
    """Raise one supplied exception after recording the runner invocation."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ResearchOutcome:
        self.calls.append(dict(kwargs))
        raise self.error
